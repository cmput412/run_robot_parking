#!/usr/bin/env python
import rospy, cv2, cv_bridge, numpy, smach, smach_ros, time, math, actionlib, tf, imutils

from geometry_msgs.msg import Twist, Pose
from sensor_msgs.msg import LaserScan, Joy, Image
from nav_msgs.msg import Odometry
from kobuki_msgs.msg import Led, BumperEvent, Sound
from ar_track_alvar_msgs.msg import AlvarMarkers
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal

from skimage import filters, morphology, measure
from shapedetector import ShapeDetector
from math import copysign


numpy.set_printoptions(threshold=numpy.nan)
counter = 0
gshape = "square"

class SleepState(smach.State):
    def __init__(self):
        smach.State.__init__(self, outcomes=['Line','Done'])
        self.led = rospy.Publisher('/mobile_base/commands/led1', Led, queue_size = 1 )
        self.rate = rospy.Rate(10)  
        self.button = rospy.Subscriber('/joy', Joy, self.button_callback)
        self.end = 0                 # used to determine if the program should exit
        self.START = 0               # used to determine if the program should begin


    def button_callback(self,msg):
        rospy.loginfo('in callback')
        if msg.buttons[0] == 1:
            self.START = 1
        if msg.buttons[1] == 1:
            self.end = 1

    def execute(self, userdata):
        rospy.loginfo('Executing sleep state')

        while not rospy.is_shutdown():
            if self.end:
                return 'Done'
            if self.START:
                return 'Line'
        return 'Done'


class LineFollow(smach.State):
    def __init__(self):
        smach.State.__init__(self, outcomes=['Scan', 'TurnCounter','TurnClock','Stop','Done'])# 'GoToParkingStart'])
        self.bridge = cv_bridge.CvBridge()
        self.led1 = rospy.Publisher('/mobile_base/commands/led1', Led, queue_size = 1 )
        self.led2 = rospy.Publisher('/mobile_base/commands/led2', Led, queue_size = 1 )
        self.image_sub = rospy.Subscriber('usb_cam/image_raw',   
                        Image,self.image_callback)
        self.cmd_vel_pub = rospy.Publisher('/mobile_base/commands/velocity',
                            Twist, queue_size=1)
        self.button = rospy.Subscriber('/joy', Joy, self.button_callback)
        self.twist= Twist()
        self.rate = rospy.Rate(10)
        self.end = 0 
        self.stop = 0
        self.M = None
        self.RM = None
        self.image = None
        self.noLine = 0
        self.t1 = None

    def execute(self, userdata):
        global counter
        rospy.loginfo('Executing Line Follow state')
        self.stop = 0 
        self.twist = Twist()
        self.noLine = 0
        self.t1 = None
        self.led1.publish(0)
        self.led2.publish(0)
        while not rospy.is_shutdown():
            if self.end:
                return 'Done'

            elif self.stop:
                if counter == 0 or counter == 2 or counter == 4 or counter == 7 or counter == 8 or counter == 9:
                    counter += 1
                    self.twist.linear.x = 0.3
                    self.cmd_vel_pub.publish(self.twist)

                    return 'TurnCounter'

                elif counter == 1 or counter == 6:
                    #just stop for a moment
                    counter += 1
                    rospy.sleep(0.5)
                    self.twist = Twist()
                    self.cmd_vel_pub.publish(self.twist)

                    return 'Stop'

                elif counter == 5:
                    #go to the parking lot
                    counter += 1
                    
                    return 'GoToParkingStart'


                elif counter == 10:
                    counter = 0
                    rospy.sleep(1)
                    self.twist = Twist()
                    self.cmd_vel_pub.publish(self.twist)

                    return 'Stop'

            elif self.noLine == 2:
                counter += 1
                self.twist.linear.x = 0.2
                self.cmd_vel_pub.publish(self.twist)
                rospy.sleep(0.2)
                self.twist = Twist()
                self.cmd_vel_pub.publish(self.twist)

                return 'Scan'

        return 'Done'

    def button_callback(self,msg):
        rospy.loginfo('in callback')
        if msg.buttons[1] == 1:
            self.end = 1

    def image_callback(self, msg):
        global counter
        self.image = self.bridge.imgmsg_to_cv2(msg,desired_encoding='bgr8')
        hsv = cv2.cvtColor(self.image, cv2.COLOR_BGR2HSV)

        lower_white = numpy.array([180,170,170])#[186,186,186])    [180,180,180] [220,220,220]    # set upper and lower range for white mask
        upper_white = numpy.array([255,255,255])#[255,255,255]) [255,255,255]
        whitemask = cv2.inRange(self.image,lower_white,upper_white)

        lower_red = numpy.array([120,130,130]) # [120,150,150]                          # set upper and lower range for red mask
        upper_red = numpy.array([180,255,255]) # [180,255,255]
        redmask = cv2.inRange(hsv,lower_red,upper_red)

  
        h, w, d =self.image.shape
        search_top = 3*h/4
        search_bot = search_top + 20

        whitemask[0:search_top, 0:w] = 0                                # search for white color
        whitemask[search_bot:h, 0:w] = 0

        redmask[0:search_top, 0:w] = 0                                  # search for red color
        redmask[search_bot:h, 0:w] = 0

        self.M = cv2.moments(whitemask)
        self.RM = cv2.moments(redmask)

        if self.RM['m00'] > 0:
            self.noLine = 0
            self.stop = 1
            self.twist.linear.x = 0.3
            self.cmd_vel_pub.publish(self.twist)

        elif self.M['m00'] > 0 and self.stop == 0:
            rospy.loginfo("Line found")
            self.noLine = 0
            self.PID_Controller(w)

        else:
            rospy.loginfo("no line")
            if self.noLine == 0:
                self.t1 = rospy.Time.now() + rospy.Duration(2)
                self.noLine = 1
            elif self.noLine == 1 and (self.t1 <= rospy.Time.now()):
                self.noLine = 2

        cv2.imshow("window", self.image)
        cv2.waitKey(3)

    def PID_Controller(self,w):

        prev_err = 0
        integral = 0
        dt = 1

        cx = int(self.M['m10']/self.M['m00'])
        cy = int(self.M['m01']/self.M['m00'])
        cv2.circle(self.image, (cx, cy), 20, (0,0,255),-1)
        err = cx - w/2
        Kp = .0035 
        Ki = 0
        Kd = .001
        integral = integral + err * dt
        derivative = (err-prev_err) / dt
        prev_err = err
        output = (err * Kp) + (integral * Ki) + (derivative * Kd)
        self.twist.linear.x = 0.3
        self.twist.angular.z =  -output
        self.cmd_vel_pub.publish(self.twist)


class StopState(smach.State):
    def __init__(self):
        smach.State.__init__(self, outcomes=['Line','Done'])
        self.cmd_vel_pub = rospy.Publisher('/mobile_base/commands/velocity',
                            Twist, queue_size=1)
        self.button = rospy.Subscriber('/joy', Joy, self.button_callback)
        self.end = 0
        self.twist = Twist()

    def button_callback(self,msg):
        rospy.loginfo('in callback')
        if msg.buttons[1] == 1:
            self.end = 1

    def execute(self,userdata):
        rospy.loginfo('Executing Stop state')
        self.twist = Twist()
        while not rospy.is_shutdown():
            time = rospy.Time.now() + rospy.Duration(2)
            while rospy.Time.now() < time:
                self.twist.linear.x = 0
                self.cmd_vel_pub.publish(self.twist)
                if self.end:
                    return 'Done'
            self.twist.linear.x = 0.3
            self.cmd_vel_pub.publish(self.twist)
            rospy.sleep(.5)
            return 'Line'
        return 'Done'


class Turn90Clockwise(smach.State):
    def __init__(self):
        smach.State.__init__(self, outcomes=['Line','Done'])
        self.cmd_vel_pub = rospy.Publisher('/mobile_base/commands/velocity',
                            Twist, queue_size=1)
        self.button = rospy.Subscriber('/joy', Joy, self.button_callback)
        self.end = 0
        self.twist = Twist()
        self.speed = -45
        self.angle = 90
        self.angular_speed = self.speed*2*math.pi/360
        self.relative_angle = self.angle*2.3*math.pi/360
        self.mult = 1.2
        self.mult2 = 1

    def button_callback(self,msg):
        rospy.loginfo('in callback')
        if msg.buttons[1] == 1:
            self.end = 1

    def execute(self,userdata):
        rospy.loginfo('Executing Turn90Clockwise state')

        global counter
        self.angular_speed = self.speed*2*math.pi/360
        self.relative_angle = self.angle*2.3*math.pi/360

        if counter == 9:
            self.relative_angle = self.relative_angle * self.mult
        if counter == 8:
            self.relative_angle = self.relative_angle * self.mult2

        self.twist = Twist()

        while not rospy.is_shutdown():

            current_angle = 0
            self.twist.angular.z = self.angular_speed
            self.cmd_vel_pub.publish(self.twist)
            t0 = rospy.Time.now().to_sec()

            while(current_angle < self.relative_angle):
                self.cmd_vel_pub.publish(self.twist)
                t1 = rospy.Time.now().to_sec()
                current_angle = abs(self.angular_speed)*(t1-t0)

            return 'Line'

        return 'Done'


class Turn90CounterClockwise(smach.State):
    def __init__(self):
        smach.State.__init__(self, outcomes=['Read', 'Scan', 'Line','Done'])
        self.cmd_vel_pub = rospy.Publisher('/mobile_base/commands/velocity',
                            Twist, queue_size=1)
        self.button = rospy.Subscriber('/joy', Joy, self.button_callback)
        self.end = 0
        self.twist = Twist()
        self.speed = 45
        self.angle = 90
        self.angular_speed = self.speed*2*math.pi/360
        self.relative_angle = self.angle*2.3*math.pi/360
        self.mult = 1.2
        self.mult2 = 0.8


    def button_callback(self,msg):
        rospy.loginfo('in callback')
        if msg.buttons[1] == 1:
            self.end = 1

    def execute(self,userdata):
        global counter
        self.angular_speed = self.speed*2*math.pi/360
        self.relative_angle = self.angle*2.3*math.pi/360
        if counter == 9:
            self.relative_angle = self.relative_angle * self.mult
        if counter == 8:
            self.relative_angle = self.relative_angle * self.mult2


        rospy.loginfo('Executing Turn90 state')
        self.twist = Twist()
        self.twist.linear.x =.3

        if counter == 9:
            self.twist.angular.z = 0.6
        self.cmd_vel_pub.publish(self.twist)
        t0 = rospy.Time.now() + rospy.Duration(3)

        while t0 > rospy.Time.now():
            x = 0
        self.twist = Twist()

        while not rospy.is_shutdown():
            current_angle = 0
            self.twist.angular.z = self.angular_speed
            self.cmd_vel_pub.publish(self.twist)
            t0 = rospy.Time.now().to_sec()
            while(current_angle < self.relative_angle):
                self.cmd_vel_pub.publish(self.twist)
                t1 = rospy.Time.now().to_sec()
                current_angle = abs(self.angular_speed)*(t1-t0)
            rospy.loginfo(counter)

            if counter == 1:
                return 'Scan' 
            elif counter == 8 or counter == 9 or counter == 10:
                return 'Read'

            elif counter == 3 or counter == 5:
                return 'Line'
            
        return 'Done'


class Turn180(smach.State):
    def __init__(self):
        smach.State.__init__(self, outcomes=['Line','Done'])
        self.cmd_vel_pub = rospy.Publisher('/mobile_base/commands/velocity',
                            Twist, queue_size=1)

        self.button = rospy.Subscriber('/joy', Joy, self.button_callback)
        self.end = 0
        self.twist = Twist()
        self.speed = 90
        self.angle = 180
        self.angular_speed = self.speed*2*math.pi/360
        self.relative_angle = self.angle*2.6*math.pi/360


    def button_callback(self,msg):
        rospy.loginfo('in callback')
        if msg.buttons[1] == 1:
            self.end = 1

    def execute(self,userdata):
        rospy.loginfo('Executing Turn90 state')
        self.twist = Twist()
        while not rospy.is_shutdown():

            current_angle = 0
            self.twist.angular.z = self.angular_speed
            self.cmd_vel_pub.publish(self.twist)
            t0 = rospy.Time.now().to_sec()

            while(current_angle < self.relative_angle):
                self.cmd_vel_pub.publish(self.twist)
                t1 = rospy.Time.now().to_sec()
                current_angle = self.angular_speed*(t1-t0)

            self.twist.angular.z = 0
            self.cmd_vel_pub.publish(self.twist)
            
            return 'Line'
    
        return 'Done'


class ScanObject(smach.State):
    def __init__(self):
        smach.State.__init__(self, outcomes=['Read', 'TurnClock','Done'])
        
        self.cmd_vel_pub = rospy.Publisher('/mobile_base/commands/velocity',
                            Twist, queue_size=1)
        self.image_sub = rospy.Subscriber('/camera/rgb/image_raw',   
                        Image,self.image_callback)
        self.led1 = rospy.Publisher('/mobile_base/commands/led1', Led, queue_size = 1 )
        self.led2 = rospy.Publisher('/mobile_base/commands/led2', Led, queue_size = 1 )
        self.button = rospy.Subscriber('/joy', Joy, self.button_callback)
        self.sound = rospy.Publisher('/mobile_base/commands/sound', Sound)

        self.bridge = cv_bridge.CvBridge()
        self.val = None
        self.found = 0
        self.lst = []
        self.scanTime = 0


    def button_callback(self,msg):
        rospy.loginfo('in callback')
        if msg.buttons[1] == 1:
            self.end = 1

    def execute(self,userdata):
        self.scanTime = 1
        self.lst = []
        self.found = 0
        global counter
        self.twist = Twist()
        self.cmd_vel_pub.publish(self.twist)
        while not rospy.is_shutdown():
            if self.found:
                if counter == 4:
                    self.val += 1
                if self.val == 1:
                    rospy.loginfo('here1')
                    self.led2.publish(1)
                    self.sound.publish(0)

                elif self.val == 2:
                    rospy.loginfo('here2')
                    self.led1.publish(1)
                    self.sound.publish(0)
                    rospy.sleep(0.5)
                    self.sound.publish(0)
                else:
                    rospy.loginfo('here3')
                    self.led1.publish(1)
                    self.led2.publish(1)
                    self.sound.publish(0)
                    rospy.sleep(0.5)
                    self.sound.publish(0)
                    rospy.sleep(0.5)
                    self.sound.publish(0)

                self.scanTime = 0                
                if counter == 4:
                    return 'Read'
                return 'TurnClock'

        return 'Done'

    def image_callback(self, msg):
        if self.scanTime:
            global counter
            self.image = self.bridge.imgmsg_to_cv2(msg,desired_encoding='bgr8')
            hsv = cv2.cvtColor(self.image, cv2.COLOR_BGR2HSV)
            if counter == 4:
                redmask = self.threshold_hsv_360(140,100,10,255,255,120,hsv)    # ignores green, really good for red
            else:
                redmask = self.threshold_hsv_360(30,80,20,255,255,120,hsv)
            ret, thresh = cv2.threshold(redmask, 127, 255, 0)
            im2, cnts, hierarchy = cv2.findContours(thresh,cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(redmask, cnts, -1, (0,255,0), 3)

            img = measure.label(redmask, background=0)
            img += 1
            propsa = measure.regionprops(img.astype(int))
            length = len(propsa)

            self.lst.append(length-1)

            if len(self.lst) > 7:
                self.val = self.lst[-1]
                self.found = 1

        #self.grouping += length - 1
        #self.i += 1
        #self.avg = self.grouping/self.i
        #cv2.imshow("window", redmask)
        #cv2.waitKey(3)
        

    def threshold_hsv_360(self,s_min, v_min, h_max, s_max, v_max, h_min, hsv):
        lower_color_range_0 = numpy.array([0, s_min, v_min],dtype=float)
        upper_color_range_0 = numpy.array([h_max/2., s_max, v_max],dtype=float)
        lower_color_range_360 = numpy.array([h_min/2., s_min, v_min],dtype=float)
        upper_color_range_360 = numpy.array([360/2., s_max, v_max],dtype=float)
        mask0 = cv2.inRange(hsv, lower_color_range_0, upper_color_range_0)
        mask360 = cv2.inRange(hsv, lower_color_range_360, upper_color_range_360)
        mask = mask0 | mask360
        return mask

class ReadShape(smach.State):
    def __init__(self):
        smach.State.__init__(self, outcomes=['Turn180', 'TurnClock','Done'])
        self.cmd_vel_pub = rospy.Publisher('/mobile_base/commands/velocity',
                            Twist, queue_size=1)
        self.image_sub = rospy.Subscriber('/camera/rgb/image_raw',   
                        Image,self.image_callback)
        self.led1 = rospy.Publisher('/mobile_base/commands/led1', Led, queue_size = 1 )
        self.led2 = rospy.Publisher('/mobile_base/commands/led2', Led, queue_size = 1 )
        self.sound = rospy.Publisher('/mobile_base/commands/sound', Sound)

        self.button = rospy.Subscriber('/joy', Joy, self.button_callback)
        self.bridge = cv_bridge.CvBridge()
        self.readTime = 0
        self.found = 0
        self.shape_list = list()

    def button_callback(self,msg):
        rospy.loginfo('in callback')
        if msg.buttons[1] == 1:
            self.end = 1

    def execute(self,userdata):
        global counter
        self.shape_list = list()
        rospy.sleep(1)
        self.readTime = 1
        self.lst = []
        self.twist = Twist()
        self.cmd_vel_pub.publish(self.twist)
        self.found = 0
        while not rospy.is_shutdown():
            if self.found:
                self.readTime = 0
                if counter == 4:
                    return 'Turn180'
                return 'TurnClock'

        return 'Done'

    def image_callback(self, msg):
        global counter, gshape
        if self.readTime:
            self.image = self.bridge.imgmsg_to_cv2(msg,desired_encoding='bgr8')
            hsv = cv2.cvtColor(self.image, cv2.COLOR_BGR2HSV)
            if counter == 4:
                lower_red = numpy.array([40,50,50])#[100,0,0])                   
                upper_red = numpy.array([70,255,255])#[255,30,30])
                mask = cv2.inRange(hsv,lower_red,upper_red) # green masks
            else:
                mask = self.threshold_hsv_360(140,10,10,255,255,120,hsv)
            #cv2.inRange(hsv,lower_red,upper_red)
            #rospy.loginfo(redmask)
            ret, thresh = cv2.threshold(mask, 127, 255, 0)
            im2, cnts, hierarchy = cv2.findContours(thresh,cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(mask, cnts, -1, (0,255,0), 3)
            #cnts = imutils.grab_contours(cnts)
            sd = ShapeDetector()
            shape = None
            for c in cnts:
                shape = sd.detect(c)
     
                if shape != None:
                    self.shape_list.append(shape)
                
                
            if len(self.shape_list) >= 20:
                if counter == 4:
                    gshape =  max(set(self.shape_list), key=self.shape_list.count)
                    rospy.loginfo(gshape)
                else:
                    shape =  max(set(self.shape_list), key=self.shape_list.count)
                    if gshape == shape:
                        self.led1.publish(3)
                        #TODO: make beep
                        self.sound.publish(6)
                        
                    rospy.loginfo(shape)
                self.found = 1
                
    def threshold_hsv_360(self,s_min, v_min, h_max, s_max, v_max, h_min, hsv):
        lower_color_range_0 = numpy.array([0, s_min, v_min],dtype=float)
        upper_color_range_0 = numpy.array([h_max/2., s_max, v_max],dtype=float)
        lower_color_range_360 = numpy.array([h_min/2., s_min, v_min],dtype=float)
        upper_color_range_360 = numpy.array([360/2., s_max, v_max],dtype=float)
        mask0 = cv2.inRange(hsv, lower_color_range_0, upper_color_range_0)
        mask360 = cv2.inRange(hsv, lower_color_range_360, upper_color_range_360)
        mask = mask0 | mask360
        return mask

    ###COMPETITION 3 CODE STARTS HERE ####

    ###AR TAG PART###

# found_markers = []
# start = None
# position = None
# orientation =  None

class GoToStart(smach.State):
    """
    Return to the start in order to go to next AR tag
    """
    def __init__(self):
        smach.State.__init__(self, outcomes=['FindTag','Done'])

        rospy.loginfo("In GO to start")

        rospy.loginfo("Setting up client")
        self.client = actionlib.SimpleActionClient('/move_base', MoveBaseAction)
    	rospy.loginfo("ready")
    	self.client.wait_for_server()
        rospy.loginfo("here")

        self.startPosition = None
        #TODO: set this  

        
    def execute(self,userdata):
        rospy.loginfo("Executing State GoToStart")
        self.goal = MoveBaseGoal()
        self.goal.target_pose.header.frame_id = '/odom'
        self.goal.target_pose.pose.position.x = self.start.position.x
        self.goal.target_pose.pose.position.y = self.start.position.y
        self.goal.target_pose.pose.position.z = self.start.position.z
        self.goal.target_pose.pose.orientation.x = self.start.orientation.x
        self.goal.target_pose.pose.orientation.y = self.start.orientation.y
        self.goal.target_pose.pose.orientation.z = self.start.orientation.z
        self.goal.target_pose.pose.orientation.w = self.start.orientation.w

        while not rospy.is_shutdown():
            rospy.loginfo("Executing GoToStart")
            self.client.send_goal(self.goal)
            self.client.wait_for_result()
            return 'FindTag'

        return 'Done'


class GoToWayPointAR(smach.State):
    """
    Purpose: go to  waypoint once we are within threshold
    """

    def __init__(self):
        smach.State.__init__(self, outcomes=['GoToStart','Done'])

        self.alvar_sub = rospy.Subscriber('ar_pose_marker', AlvarMarkers, self.alvarCallback)
        self.odom_sub = rospy.Subscriber('odom', Odometry, self.odomCallback)
        self.led1 = rospy.Publisher('/mobile_base/commands/led1', Led, queue_size = 1 )

        rospy.loginfo("Setting up client2")
        self.client = actionlib.SimpleActionClient('move_base', MoveBaseAction)
    	self.client.wait_for_server()
        rospy.loginfo("client Ready")

        self.pose = None
        self.goalPose = None


    def execute(self, userdata):
        rospy.loginfo("Executing GoToWayPointAR")

        while not rospy.is_shutdown():
            rospy.wait_for_message('ar_pose_marker', AlvarMarkers)
            rospy.wait_for_message('odom', Odometry)

            goal = self.calculateGoal()

            self.client.send_goal(goal)
            self.client.wait_for_result()
            self.led1.publish(1) #make the light green
            
            return 'GoToWayPoint'

        return 'done'

    def calculateGoal(self):
        t = self.pose
        distToRobot = ros_numpy.numpify(self.pose) # p2
        distToTag = ros_numpy.numpify(self.goalPose) #p1

        distToTagGlobal = numpy.dot(distToRobot, distToTag) #gives us the pose of the tag w.r.t. global frame

        distToTagGlobal = ros_numpy.msgify(Pose, distToTagGlobal)

        goal = MoveBaseGoal()
        goal.target_pose.header.frame_id = '/odom'
        goal.target_pose.pose.position.x = distToTagGlobal.position.x 
        goal.target_pose.pose.position.y = distToTagGlobal.position.y 
        goal.target_pose.pose.position.z = 0
        goal.target_pose.pose.orientation.x = self.pose.orientation.x 
        goal.target_pose.pose.orientation.y = self.pose.orientation.y
        goal.target_pose.pose.orientation.z = self.pose.orientation.z
        goal.target_pose.pose.orientation.w = self.pose.orientation.w

        quaternion = (  distToTagGlobal.orientation.x,
                        distToTagGlobal.orientation.y,
                        distToTagGlobal.orientation.z,
                        distToTagGlobal.orientation.w
                        )
        

        euler = tf.transformations.euler_from_quaternion(quaternion)
        yaw = euler[2]
        yaw -= math.pi/2

        dx = 0.1*math.cos(yaw)
        dy = 0.1*math.sin(yaw)
        
        goal.target_pose.pose.position.x += dx
        goal.target_pose.pose.position.y += dy

        return goal     


    def odomCallback(self, msg):
        self.pose = msg.pose.pose


    def alvarCallback(self, msg):
        try:
            #rospy.loginfo(msg.markers[0].id)
            marker = msg.markers[0]
            self.goalPose = marker.pose.pose
        except:
            pass

class FindTag(smach.State):
    def __init__(self):

        smach.State.__init__(self, outcomes=['ApproachTag','Done'])
        self.cmd_vel_pub = rospy.Publisher('mobile_base/commands/velocity', Twist, queue_size=5)
        self.odom_sub = rospy.Subscriber('odom', Odometry, self.odomCallback)
        rospy.Subscriber('ar_pose_marker', AlvarMarkers, self.set_cmd_vel)
        

        rospy.wait_for_message('ar_pose_marker', AlvarMarkers)
        

        self.move_cmd = Twist()  
        # Set flag to indicate when the AR marker is visible
        self.current_marker = None
        self.look_for_marker = 1
        self.now = 0
        self.pose = None


    def execute(self,userdata):
        global start
        rospy.loginfo("Executing State FindTag")

        rospy.wait_for_message('odom', Odometry)

        while not rospy.is_shutdown():
            self.now = 1
            self.move_cmd = Twist()
            self.found = 0

            while self.found == 0:
                self.cmd_vel_pub.publish(self.move_cmd)
            self.now = 0

            return 'ApproachTag'
        return 'Done'


    def set_cmd_vel(self,msg):
        # if there is a marker do try
        global found_markers
        if self.now:
            try: 
                marker = msg.markers[0]
                self.current_marker = marker.id
                if (self.current_marker not in found_markers) and (self.current_marker != 0):
                        rospy.loginfo("FOLLOWER found Target!")
                        found_markers.append(self.current_marker)
                        self.found = 1
                        rospy.loginfo(found_markers)

                else:
                    rospy.loginfo("FOLLOWER is looking for Target")
                    self.move_cmd.linear.x = 0
                    self.move_cmd.angular.z = 0.3

            except:
                self.move_cmd.linear.x = 0
                self.move_cmd.angular.z = 0.3


    def odomCallback(self, msg):
        self.pose = msg.pose.pose

class ApproachTag(smach.State):
    def __init__(self):

        smach.State.__init__(self, outcomes=['GoToWayPointAR','Done'])
        self.cmd_vel_pub = rospy.Publisher('mobile_base/commands/velocity', Twist, queue_size=5)

        self.move_cmd = Twist()
        
        # Set flag to indicate when the AR marker is visible
        self.target_visible = False
        self.current_marker = None
        self.found = 0
        self.success = 0
        self.now = 0
        self.look_for_marker = 1

        rospy.wait_for_message('ar_pose_marker',AlvarMarkers)
        rospy.loginfo("here4")
        rospy.Subscriber('ar_pose_marker', AlvarMarkers, self.set_cmd_vel)
        

    def execute(self,userdata):

        rospy.loginfo("Executing State ApproachTag")
        
        while not rospy.is_shutdown():
            self.now = 1
            self.move_cmd = Twist()
            self.success = 0
            while self.success == 0:
                self.cmd_vel_pub.publish(self.move_cmd)
            self.now = 0
            return 'GoToWayPointAR'
        return 'Done'


    def set_cmd_vel(self,msg):
        if self.now:
            try: 
                marker = msg.markers[0]
                self.current_marker = marker.id
                if not self.target_visible:
                        rospy.loginfo("FOLLOWER is Tracking Target!")
                self.target_visible = True

            except:
                    self.move_cmd.linear.x /= 1.5
                    self.move_cmd.angular.z /= 1.5
                        
                    if self.target_visible:
                        rospy.loginfo("FOLLOWER LOST Target!")
                    self.target_visible = False

                    return 

            # Get the displacement of the marker relative to the base
            target_offset_y = marker.pose.pose.position.y
            # Get the distance of the marker from the base
            target_offset_x = marker.pose.pose.position.x

            # keep ar tag in centre
            if target_offset_y > 0.1:
                speed = 0.2
            elif target_offset_y < -0.1:
                speed = -0.2
            else:
                speed = 0
            self.move_cmd.angular.z = speed

            if target_offset_x > 0.7:
                speed = 0.2
                if speed <0:
                    speed *= 1.5
            else:
                speed = 0
                self.success = 1

            self.move_cmd.linear.x = speed

    ### Go to Predetermined Waypoint ###

#class GoToWayPoint(smach.state):
#    def __init__():
#	pass
	
        

    

def main():
    rospy.init_node('Comp3')
    rate = rospy.Rate(10)
    sm = smach.StateMachine(outcomes = ['DoneProgram'])
    sm.set_initial_state(['LineFollow'])

    with sm:
        
        #Compeition 2 states and transitions 
        smach.StateMachine.add('SleepState', SleepState(),
                                        transitions = {'Line': 'LineFollow',
                                                        'Done' : 'DoneProgram'})

        smach.StateMachine.add('LineFollow', LineFollow(),
                                        transitions = { 'Scan': 'ScanObject',
                                                        'TurnCounter': 'Turn90CounterClockwise',
                                                        'TurnClock': 'Turn90Clockwise',
                                                        'Stop': 'StopState',
                                                        #'GoToParkingStart' :  'GoToStart',
                                                        'Done' : 'DoneProgram'})

        smach.StateMachine.add('StopState', StopState(),
                                        transitions = {'Line': 'LineFollow',

                                                        'Done' : 'DoneProgram'})

        smach.StateMachine.add('Turn90Clockwise', Turn90Clockwise(),
                                        transitions = {'Line': 'LineFollow',
                                                        'Done' : 'DoneProgram'})

        smach.StateMachine.add('Turn90CounterClockwise', Turn90CounterClockwise(),
                                        transitions = { 'Read': 'ReadShape',
                                                        'Scan': 'ScanObject',
                                                        'Line': 'LineFollow',
                                                        'Done' : 'DoneProgram'})

        smach.StateMachine.add('Turn180', Turn180(),
                                        transitions = {'Line': 'LineFollow',
                                                        'Done' : 'DoneProgram'})

        smach.StateMachine.add('ScanObject', ScanObject(),
                                        transitions = { 'Read': 'ReadShape',
                                                        'TurnClock': 'Turn90Clockwise',
                                                        'Done' : 'DoneProgram'})

        smach.StateMachine.add('ReadShape', ReadShape(),
                                        transitions = { 'Turn180': 'Turn180',
                                                        'TurnClock': 'Turn90Clockwise',
                                                        'Done' : 'DoneProgram'})

    #    #AR tag-related states and transitions 
   #     smach.StateMachine.add('GoToStart', GoToStart(),
  #                                      transitions = {'FindTag': 'FindTag',
 #                                                       'Done' : 'DoneProgram'})
#
   #     smach.StateMachine.add('GoToWayPointAR', GoToWayPointAR(),
  #                                      transitions = {'GoToWayPoint': 'GoToWayPoint',
 #                                                       'Done' : 'DoneProgram'})
#
   #     smach.StateMachine.add('ApproachTag', ApproachTag(),
  #                                      transitions = {'GoToWayPointAR': 'GoToWayPointAR',
 #                                                       'Done' : 'DoneProgram'})
#
 #       smach.StateMachine.add('FindTag', FindTag(),
 #                                       transitions = {'ApproachTag': 'ApproachTag',
 #                                                       'Done' : 'DoneProgram'})
    sis = smach_ros.IntrospectionServer('server_name', sm, '/SM_ROOT')
    

    sis.start()
    
    outcome = sm.execute() 
    # Wait for ctrl-c to stop the application
    rospy.spin()
    sis.stop()

if __name__ == '__main__':
    main()