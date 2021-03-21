#!/usr/bin/env python

# task: pointcloud exploration
from __future__ import print_function
from __future__ import division

import cv2
import numpy as np
import random
import scipy.ndimage


import rospy
from geometry_msgs.msg import PointStamped, Point
from sensor_msgs.msg import PointCloud2, Image
from nav_msgs.msg import Odometry
import ros_numpy
import tf
from cv_bridge import CvBridge, CvBridgeError

from drdo_exploration.msg import direction

class Exploration:
  def __init__(self):

    self.curr_position = np.zeros(3)
    self.curr_orientation = np.zeros(3)
    self.init_pose = None
    self.pc2_arr = None
    self.listener = tf.TransformListener()

    pc2_topic = '/depth_camera/depth/points'
    pose_topic = '/mavros/global_position/local'
    pc2_img_topic = '/depth_camera/depth/image_raw'
    rospy.Subscriber(pc2_img_topic, Image, self.pc2ImageCallback)
    rospy.Subscriber(pc2_topic, PointCloud2, self.pc2Callback)
    rospy.Subscriber(pose_topic, Odometry, self.positionCallback)
    dirn_topic = '/target_vector'
    self.pub = rospy.Publisher(dirn_topic, direction, queue_size=10)

    KERNEL_SIZE = 640
    decay_sequence = np.ones(KERNEL_SIZE//2, dtype=float)/(1+np.arange(KERNEL_SIZE//2))
    self.kernel_right = np.concatenate((np.zeros(KERNEL_SIZE//2),
                                        decay_sequence))
    self.kernel_left = self.kernel_right[::-1]


  def positionCallback(self, local_pose_msg):
    self.curr_position = [local_pose_msg.pose.pose.position.x,
                          local_pose_msg.pose.pose.position.y,
                          local_pose_msg.pose.pose.position.z]
    quaternion = [local_pose_msg.pose.pose.orientation.x,
                   local_pose_msg.pose.pose.orientation.y,
                   local_pose_msg.pose.pose.orientation.z,
                   local_pose_msg.pose.pose.orientation.w]

    self.curr_orientation = tf.transformations.euler_from_quaternion(quaternion)
    # print(self.curr_position, self.curr_orientation)


  def pc2Callback(self, pc2_msg):
    self.pc2_arr = ros_numpy.numpify(pc2_msg)


  # def pc2Callback(self, pc2_msg):
  #   xyz_array = ros_numpy.point_cloud2.pointcloud2_to_xyz_array(pc2_msg)
  #   mask = (xyz_array[:,2] < 4.5) & (xyz_array[:,2] > 0.5)
  #   xyz_array = xyz_array[mask]
  #   dist = np.linalg.norm(xyz_array, axis=1)
  #   # print(max(dist))
  #   # print("Received")
  #   # print(len(xyz_array))


  def pc2ImageCallback(self, pc2_img_msg):
    bridge = CvBridge()
    pc2_img_msg.encoding = "32FC1"
    try:
      cv_img = bridge.imgmsg_to_cv2(pc2_img_msg, pc2_img_msg.encoding)
    except CvBridgeError as e:
      print(e)
      return
    
    cv_image_array = np.array(cv_img, dtype = np.dtype('f8'))
    POINTCLOUD_CUTOFF = 10
    cv_image_norm = cv_image_array/POINTCLOUD_CUTOFF

    

    cleaned_cv_img = cv_image_norm.copy()
    cleaned_cv_img[np.isnan(cleaned_cv_img)] = 1.0

    
    cleaned_cv_img = self.filterSkyGround(cleaned_cv_img)


    penalized_cv_img = self.penalizeObstacleProximity(cleaned_cv_img)


    target = self.findTarget(penalized_cv_img)

    cv2.circle(penalized_cv_img, (target[1],target[0]), 20, 0, -1)
    cv2.imshow("Penalized image", penalized_cv_img)
    cv2.waitKey(3)

    ps = self.pixel_to_dirn(target[0],target[1])
    dirn = np.array([ps.point.x, ps.point.y, ps.point.z])
    dirn = 1.*dirn/np.linalg.norm(dirn)

    dirn_msg = direction()
    dirn_msg.vec_x = dirn[0]
    dirn_msg.vec_y = dirn[1]
    dirn_msg.vec_z = dirn[2]
    
    print("%.2f %.2f %.2f"%(dirn[0], -dirn[1], -dirn[2]))
    
    self.pub.publish(dirn_msg)
    
  def filterSkyGround(self, cleaned_cv_img):
    ## Filtering sky and ground ==> dont_see_mask -----------------------------------------
    
    height, width = [480, 640]
    '''
    I have assumed that the origin is at the top left corner.
    '''
    FOCAL_LENGTH = 554.25 # From camera_info
    LOWER_LIMIT = 0.5
    UPPER_LIMIT= 4.5
    IMAGE_PLANE_DISTANCE = 10
    '''
    Half height is the original height in meters when the distance is 10m.
    '''
    HALF_PIXELS = height/2
    # HALF_HEIGHT = (HALF_PIXELS/FOCAL_LENGTH)*IMAGE_PLANE_DISTANCE 


    sky_ground_mask = np.ones(cleaned_cv_img.shape, dtype=bool)

    '''
    1. For upper limit, the range is 0 to (image_H_PIXELS - (half_pixels+  rest pixels))
    This rest_pixels is calculated usng the given equation
    2. For lower limit, the range is half_pixels+remaining to image_H_PIXELS.
    The remaining is calculated using the given equation.
    '''
    sky_limit = int((HALF_PIXELS-(UPPER_LIMIT-self.curr_position[2])*FOCAL_LENGTH/IMAGE_PLANE_DISTANCE))
    ground_limit = int(HALF_PIXELS+((self.curr_position[2]-LOWER_LIMIT)*FOCAL_LENGTH/IMAGE_PLANE_DISTANCE))
    if sky_limit>=0 and sky_limit<height:
      sky_ground_mask[:sky_limit,:] = 0
    if ground_limit>=0 and ground_limit<height:
      sky_ground_mask[ground_limit:,:] = 0

    temp_cv_img = cleaned_cv_img.copy()
    cleaned_cv_img = np.multiply(temp_cv_img,sky_ground_mask)

    # cv2.imshow("After masking image", cleaned_cv_img.astype(float))
    # cv2.waitKey(3)

    return cleaned_cv_img


  def pixel_to_dirn(self, h, w):
    height, width = [480, 640]
    target_px = np.array([h-height//2, w-width//2])
    
    FOCAL_LENGTH = 554.25 # From camera_info
    IMAGE_PLANE_DISTANCE = 10
    xp = (IMAGE_PLANE_DISTANCE/FOCAL_LENGTH)*target_px[1]
    yp = (IMAGE_PLANE_DISTANCE/FOCAL_LENGTH)*target_px[0]
    zp = IMAGE_PLANE_DISTANCE

    # print(xp, yp, zp)

    ps = PointStamped()
    ps.header.frame_id = "depth_cam_link"
    ps.header.stamp = rospy.Time(0)
    ps.point.x = zp
    ps.point.y = -xp
    ps.point.z = -yp
    # mat = self.listener.transformPoint("/map", ps)
    # return mat
    return ps

  def pixel_to_depth(self, h, w):     #h,w are image coordinates
    # print(h,w)
    
    xp = self.pc2_arr['x'][h][w]
    yp = self.pc2_arr['y'][h][w]
    zp = self.pc2_arr['z'][h][w]  


    ps = PointStamped()
    ps.header.frame_id = "depth_cam_link"
    ps.header.stamp = rospy.Time(0)
    ps.point.x = zp
    ps.point.y = -xp
    ps.point.z = -yp
    mat = self.listener.transformPoint("/map", ps)
    return mat

  def findTarget(self, penalized_cv_img):
    '''
    Find (u,v) pixel coordinates that's the
    best candidate for target
    '''
    height, width = penalized_cv_img.shape
    max_intensity = np.max(penalized_cv_img)
    candidates = penalized_cv_img == max_intensity
    candidates = candidates.astype(float)

    nonzero_candidates = candidates.nonzero()
    idx = random.randint(0, len(nonzero_candidates[0])-1)
    target = np.array([nonzero_candidates[0][idx],
                       nonzero_candidates[1][idx]])

    return target


  def penalizeObstacleProximity(self, cleaned_cv_img):
    penalized_cv_img = cleaned_cv_img.copy()
    
    '''
    Calculate horizontal differences only finding increasing brightnesses
    ----------
    Increasing brightness => Darker(closer) to brighter(farther)
    So danger obstacle is on the left of the edge line
    '''
    right_vertical_edge = cleaned_cv_img[:,1:] - cleaned_cv_img[:,0:-1]
    right_vertical_edge = right_vertical_edge.clip(min=0)
    right_vertical_mask = (right_vertical_edge > 0.1).astype(float)
    # This matrix is basically blips at the pixels of right_vertical_edge
    
    K = 10
    right_vertical_penalty = K*scipy.ndimage.convolve1d(right_vertical_mask,
          weights= self.kernel_right, mode='constant', cval=0, axis=1)

    '''
    Calculate horizontal differences only finding decreasing brightnesses
    ----------
    Decreasing brightness => Brighter(farther) to darker(closer)
    So danger obstacle is on the right of the edge line
    '''
    left_vertical_edge = cleaned_cv_img[:,0:-1] - cleaned_cv_img[:,1:]
    left_vertical_edge = left_vertical_edge.clip(min=0)
    left_vertical_mask = (left_vertical_edge > 0.1).astype(float)
    # This matrix is basically blips at the pixels of left_vertical_edge

    left_vertical_penalty = K*scipy.ndimage.convolve1d(left_vertical_mask,
          weights= self.kernel_left, axis=1)
    
    penalized_cv_img[:,0:-1] = penalized_cv_img[:,0:-1] - right_vertical_penalty
    penalized_cv_img[:,1:] = penalized_cv_img[:,1:] - left_vertical_penalty
    # penalized_cv_img = penalized_cv_img.clip(min=0)

    penalized_cv_img[:,0] = np.zeros(480)
    penalized_cv_img[:,-1] = np.zeros(480)
    # print(penalized_cv_img[0,400:])
    # cv2.imshow("Penalized image", penalized_cv_img)
    # cv2.waitKey(3)

    ## Penalize distance from horizontal centerline

    y_dist_penalty = np.arange(480) - 479/2.
    y_dist_penalty = np.abs(y_dist_penalty)
    y_dist_penalty = np.matlib.repmat(y_dist_penalty,640,1).T
    # print(y_dist_penalty.shape)
    
    K_cam = 1e-1
    y_dist_penalty = K_cam*y_dist_penalty/(np.max(y_dist_penalty))
    penalized_cv_img = penalized_cv_img - y_dist_penalty
    penalized_cv_img.clip(min=0)


    ## Penalize deviation of z-coordinate from Z_REF

    Z_REF = 2.5
    K_altitude = 1

    err = (self.curr_position[2]-Z_REF)/Z_REF
    z_penalty = K_altitude*np.arange(480)*np.abs(err)/480
    if err>0:
      z_penalty = z_penalty[::-1]

    z_penalty = np.matlib.repmat(z_penalty,640,1).T
    penalized_cv_img = penalized_cv_img - z_penalty
    penalized_cv_img.clip(min=0)


    # cv2.imshow("Y dist penalty", penalized_cv_img)
    # cv2.waitKey(3)

    return penalized_cv_img
    

if __name__ == '__main__':
  try:
    rospy.init_node('pc2xyz_node')
    exploration = Exploration()    
    rospy.spin()
  except rospy.ROSInterruptException:
    rospy.loginfo("node terminated.")







  # def bloatImage(self, cleaned_cv_img):
  #   bloated_cv_img = cleaned_cv_img.copy()
  #   SAFETY_THRESHOLD = 200 # in pixels (needs tuning)
    
  #   '''
  #   Calculate horizontal differences only finding decreasing brightnesses
  #   ----------
  #   Decreasing brightness => Brighter(farther) to darker(closer)
  #   So danger obstacle is on the right of the edge line
  #   '''
  #   left_vertical_edge = cleaned_cv_img[:,0:-1] - cleaned_cv_img[:,1:]
  #   left_vertical_edge = left_vertical_edge.clip(min=0)
  #   left_vertical_mask = left_vertical_edge > 0.1
  #   kernel = np.concatenate((np.ones(SAFETY_THRESHOLD//2),
  #                           np.zeros(SAFETY_THRESHOLD//2)))
  #   left_vertical_mask = scipy.ndimage.convolve1d(left_vertical_mask, weights=kernel, axis=1)
  #   left_vertical_mask = left_vertical_mask > 0.1

  #   bloated_cv_img[:,1:][left_vertical_mask] = 0

  #   '''
  #   Calculate horizontal differences only finding increasing brightnesses
  #   ----------
  #   Increasing brightness => Darker(closer) to brighter(farther)
  #   So danger obstacle is on the left of the edge line
  #   '''
  #   right_vertical_edge = cleaned_cv_img[:,1:] - cleaned_cv_img[:,0:-1]
  #   right_vertical_edge = right_vertical_edge.clip(min=0)
  #   right_vertical_mask = right_vertical_edge > 0.1
  #   kernel = np.concatenate((np.zeros(SAFETY_THRESHOLD//2),
  #                           np.ones(SAFETY_THRESHOLD//2)))
  #   right_vertical_mask = scipy.ndimage.convolve1d(right_vertical_mask, weights=kernel, axis=1)
  #   right_vertical_mask = right_vertical_mask > 0.1

  #   bloated_cv_img[:,0:-1][right_vertical_mask] = 0

  #   return bloated_cv_img