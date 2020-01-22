# Original Code from
# https://github.com/tensorflow/models/blob/master/research/object_detection/object_detection_tutorial.ipynb
# Licensed under Apache 2.0 (Most likely, since tensorflow is Apache 2.0)
# Modifications Copyright (C) 2018-2020 University of Waikato, Hamilton, NZ
#
# Performs predictions on combined images from all images present in the folder passed as "prediction_in", then outputs the results as
# csv files in the folder passed as "prediction_out"
#
# The number of images to combine at a time before prediction can be specified (passed as a parameter)
# Score threshold can be specified (passed as a parameter) to ignore all rois with low score
# Can run in a continuous mode, where it will run indefinitely

import numpy as np
import os
import sys
import tensorflow as tf
import argparse
from PIL import Image
from tensorflow import Graph
from datetime import datetime
import time
import traceback
from image_complete import auto
from wai.annotations.image_utils import image_to_numpyarray, remove_alpha_channel, mask_to_polygon, polygon_to_minrect, polygon_to_lists

sys.path.append("..")
from object_detection.utils import ops as utils_ops
from object_detection.utils import label_map_util

OUTPUT_COMBINED = False
""" Whether to output CSV file with ROIs for combined images as well (only for debugging). """

SUPPORTED_EXTS = [".jpg", ".jpeg", ".png", ".bmp"]
""" supported file extensions (lower case). """

MAX_INCOMPLETE = 3
""" the maximum number of times an image can return 'incomplete' status before getting moved/deleted. """


def run_inference_for_single_image(image, graph, sess):
    """
    Obtain predictions for image.

    :param image: the image to generate predictions for
    :type image: str
    :param graph: the graph to use
    :type graph: tf.Graph()
    :param sess: the tensorflow session
    :type sess: tf.Session
    :return: the predictions
    :rtype: dict
    """

    # Get handles to input and output tensors
    ops = graph.get_operations()
    all_tensor_names = {output.name for op in ops for output in op.outputs}
    tensor_dict = {}
    for key in [
        'num_detections', 'detection_boxes', 'detection_scores',
        'detection_classes', 'detection_masks'
    ]:
        tensor_name = key + ':0'
        if tensor_name in all_tensor_names:
            tensor_dict[key] = graph.get_tensor_by_name(
                tensor_name)
    if 'detection_masks' in tensor_dict:
        # The following processing is only for single image
        detection_boxes = tf.squeeze(tensor_dict['detection_boxes'], [0])
        detection_masks = tf.squeeze(tensor_dict['detection_masks'], [0])
        # Reframe is required to translate mask from box coordinates to image coordinates and fit the image size.
        real_num_detection = tf.cast(tensor_dict['num_detections'][0], tf.int32)
        detection_boxes = tf.slice(detection_boxes, [0, 0], [real_num_detection, -1])
        detection_masks = tf.slice(detection_masks, [0, 0, 0], [real_num_detection, -1, -1])
        detection_masks_reframed = utils_ops.reframe_box_masks_to_image_masks(
            detection_masks, detection_boxes, image.shape[0], image.shape[1])
        detection_masks_reframed = tf.cast(
            tf.greater(detection_masks_reframed, 0.5), tf.uint8)
        # Follow the convention by adding back the batch dimension
        tensor_dict['detection_masks'] = tf.expand_dims(
            detection_masks_reframed, 0)
    image_tensor = graph.get_tensor_by_name('image_tensor:0')

    # Run inference
    output_dict = sess.run(tensor_dict,
                           feed_dict={image_tensor: np.expand_dims(image, 0)})

    # all outputs are float32 numpy arrays, so convert types as appropriate
    output_dict['num_detections'] = int(output_dict['num_detections'][0])
    output_dict['detection_classes'] = output_dict[
        'detection_classes'][0].astype(np.uint8)
    output_dict['detection_boxes'] = output_dict['detection_boxes'][0]
    output_dict['detection_scores'] = output_dict['detection_scores'][0]
    if 'detection_masks' in output_dict:
        output_dict['detection_masks'] = output_dict['detection_masks'][0]
    return output_dict


def load_frozen_graph(graph_path):
    """
    Loads the provided frozen graph into detection_graph to use with prediction.

    :param graph_path: the path to the frozen graph
    :type graph_path: str
    :return: the graph
    :rtype: tf.Graph
    """

    graph = tf.Graph()  # type: Graph
    with graph.as_default():
        od_graph_def = tf.compat.v1.GraphDef()
        with tf.io.gfile.GFile(graph_path, 'rb') as fid:
            serialized_graph = fid.read()
            od_graph_def.ParseFromString(serialized_graph)
            tf.import_graph_def(od_graph_def, name='')
    return graph


def predict_on_images(input_dir, graph, sess, output_dir, tmp_dir, score_threshold, categories, num_imgs, inference_times,
                      delete_input, output_polygons, mask_threshold, mask_nth, output_minrect):
    """
    Method performing predictions on all images ony by one or combined as specified by the int value of num_imgs.

    :param input_dir: the directory with the images
    :type input_dir: str
    :param graph: the graph to use
    :type graph: tf.Graph()
    :param sess: the tensorflow session
    :type sess: tf.Session
    :param output_dir: the output directory to move the images to and store the predictions
    :type output_dir: str
    :param tmp_dir: the temporary directory to store the predictions until finished
    :type tmp_dir: str
    :param score_threshold: the minimum score predictions have to have
    :type score_threshold: float
    :param categories: the label map
    :param num_imgs: the number of images to combine into one before presenting to graph
    :type num_imgs: int
    :param inference_times: whether to output a CSV file with the inference times
    :type inference_times: bool
    :param delete_input: whether to delete the input images rather than moving them to the output directory
    :type delete_input: bool
    :param output_polygons: whether the model predicts masks and polygons should be stored in the CSV files
    :type output_polygons: bool
    :param mask_threshold: the threshold to use for determining the contour of a mask
    :type mask_threshold: float
    :param mask_nth: to speed up polygon computation, use only every nth row and column from mask
    :type mask_nth: int
    :param output_minrect: when predicting polygons, whether to output the minimal rectangles around the objects as well
    :type output_minrect: bool
    """

    # Iterate through all files present in "test_images_directory"
    total_time = 0
    if inference_times:
        times = list()
        times.append("Image(s)_file_name(s),Total_time(ms),Number_of_images,Time_per_image(ms)\n")

    # counter for keeping track of images that cannot be processed
    incomplete_counter = dict()

    while True:
        start_time = datetime.now()
        im_list = []
        # Loop to pick up images equal to num_imgs or the remaining images if less
        for image_path in os.listdir(input_dir):
            # Load images only
            ext_lower = os.path.splitext(image_path)[1]
            if ext_lower in SUPPORTED_EXTS:
                full_path = os.path.join(input_dir, image_path)
                if auto.is_image_complete(full_path):
                    im_list.append(full_path)
                else:
                    if not full_path in incomplete_counter:
                        incomplete_counter[full_path] = 1
                    else:
                        incomplete_counter[full_path] = incomplete_counter[full_path] + 1

            # remove images that cannot be processed
            remove_from_blacklist = []
            for k in incomplete_counter:
                if incomplete_counter[k] == MAX_INCOMPLETE:
                    print("%s - %s" % (str(datetime.now()), os.path.basename(k)))
                    remove_from_blacklist.append(k)
                    try:
                        if delete_input:
                            print("  flagged as incomplete {} times, deleting\n".format(MAX_INCOMPLETE))
                            os.remove(k)
                        else:
                            print("  flagged as incomplete {} times, skipping\n".format(MAX_INCOMPLETE))
                            os.rename(k, os.path.join(output_dir, os.path.basename(k)))
                    except:
                        print(traceback.format_exc())

            for k in remove_from_blacklist:
                del incomplete_counter[k]

            if len(im_list) == num_imgs:
                break

        if len(im_list) == 0:
            time.sleep(1)
            break
        else:
            print("%s - %s" % (str(datetime.now()), ", ".join(os.path.basename(x) for x in im_list)))

        try:
            # Combining picked up images
            i = len(im_list)
            combined = []
            comb_img = None
            if i > 1:
                while i != 0:
                    if comb_img is None:
                        img2 = Image.open(im_list[i-1])
                        img1 = Image.open(im_list[i-2])
                        i -= 1
                        combined.append(os.path.join(output_dir, "combined.png"))
                    else:
                        img2 = comb_img
                        img1 = Image.open(im_list[i-1])
                    i -= 1
                    # Remove alpha channel if present
                    img1 = remove_alpha_channel(img1)
                    img2 = remove_alpha_channel(img2)
                    w1, h1 = img1.size
                    w2, h2 = img2.size
                    comb_img = np.zeros((h1+h2, max(w1, w2), 3), np.uint8)
                    comb_img[:h1, :w1, :3] = img1
                    comb_img[h1:h1+h2, :w2, :3] = img2
                    comb_img = Image.fromarray(comb_img)

            if comb_img is None:
                im_name = im_list[0]
                image = Image.open(im_name)
                image = remove_alpha_channel(image)
            else:
                im_name = combined[0]
                image = remove_alpha_channel(comb_img)

            image_np = image_to_numpyarray(image)
            output_dict = run_inference_for_single_image(image_np, graph, sess)

            # Loading results
            boxes = output_dict['detection_boxes']
            scores = output_dict['detection_scores']
            classes = output_dict['detection_classes']

            if OUTPUT_COMBINED:
                roi_path = "{}/{}-rois-combined.csv".format(output_dir, os.path.splitext(os.path.basename(im_name))[0])
                with open(roi_path, "w") as roi_file:
                    # File header
                    roi_file.write("file,x0,y0,x1,y1,x0n,y0n,x1n,y1n,label,label_str,score")
                    if output_polygons:
                        roi_file.write(",poly_x,poly_y,poly_xn,poly_yn")
                        if output_minrect:
                            roi_file.write(",minrect_w,minrect_h")
                    roi_file.write("\n")
                    for index in range(output_dict['num_detections']):
                        score = scores[index]

                        # Ignore this roi if the score is less than the provided threshold
                        if score < score_threshold:
                            continue

                        y0n, x0n, y1n, x1n = boxes[index]
                        label = classes[index]
                        label_str = categories[label - 1]['name']

                        # Translate roi coordinates into image coordinates
                        x0 = x0n * image.width
                        y0 = y0n * image.height
                        x1 = x1n * image.width
                        y1 = y1n * image.height

                        if output_polygons:
                            px = []
                            py = []
                            pxn = []
                            pyn = []
                            bw = ""
                            bh = ""
                            if 'detection_masks'in output_dict:
                                poly = mask_to_polygon(output_dict['detection_masks'][index], mask_threshold=mask_threshold, mask_nth=mask_nth)
                                if len(poly) > 0:
                                    px, py = polygon_to_lists(poly[0], swap_x_y=True, normalize=False, as_string=True)
                                    pxn, pyn = polygon_to_lists(poly[0], swap_x_y=True, normalize=True, img_width=image.width, img_height=image.height, as_string=True)
                                    if output_minrect:
                                        bw, bh = polygon_to_minrect(poly[0])

                        roi_file.write(
                            "{},{},{},{},{},{},{},{},{},{},{},{}".format(os.path.basename(im_name), x0, y0, x1, y1,
                                                                           x0n, y0n, x1n, y1n, label, label_str, score))
                        if output_polygons:
                            roi_file.write(',"{}","{}","{}","{}"'.format(",".join(px), ",".join(py), ",".join(pxn), ",".join(pyn)))
                            if output_minrect:
                                roi_file.write(',"{}","{}"'.format(bw, bh))
                        roi_file.write("\n")

            # Code for splitting rois to multiple csv's, one csv per image before combining
            max_height = 0
            prev_min = 0
            for i in range(len(im_list)):
                img = Image.open(im_list[i])
                img_height = img.height
                min_height = prev_min
                max_height += img_height
                prev_min = max_height
                roi_path = "{}/{}-rois.csv".format(output_dir, os.path.splitext(os.path.basename(im_list[i]))[0])
                if tmp_dir is not None:
                    roi_path_tmp = "{}/{}-rois.tmp".format(tmp_dir, os.path.splitext(os.path.basename(im_list[i]))[0])
                else:
                    roi_path_tmp = "{}/{}-rois.tmp".format(output_dir, os.path.splitext(os.path.basename(im_list[i]))[0])
                with open(roi_path_tmp, "w") as roi_file:
                    # File header
                    roi_file.write("file,x0,y0,x1,y1,x0n,y0n,x1n,y1n,label,label_str,score")
                    if output_polygons:
                        roi_file.write(",poly_x,poly_y,poly_xn,poly_yn")
                        if output_minrect:
                            roi_file.write(",minrect_w,minrect_h")
                    roi_file.write("\n")
                    # rois
                    for index in range(output_dict['num_detections']):
                        score = scores[index]

                        # Ignore this roi if the score is less than the provided threshold
                        if score < score_threshold:
                            continue

                        y0n, x0n, y1n, x1n = boxes[index]

                        # Translate roi coordinates into combined image coordinates
                        x0 = x0n * image.width
                        y0 = y0n * image.height
                        x1 = x1n * image.width
                        y1 = y1n * image.height

                        if y0 > max_height or y1 > max_height:
                            continue
                        elif y0 < min_height or y1 < min_height:
                            continue

                        label = classes[index]
                        label_str = categories[label - 1]['name']

                        if output_polygons:
                            px = []
                            py = []
                            pxn = []
                            pyn = []
                            bw = ""
                            bh = ""
                            if 'detection_masks'in output_dict:
                                poly = mask_to_polygon(output_dict['detection_masks'][index], mask_threshold=mask_threshold, mask_nth=mask_nth)
                                if len(poly) > 0:
                                    px, py = polygon_to_lists(poly[0], swap_x_y=True, normalize=False, as_string=True)
                                    pxn, pyn = polygon_to_lists(poly[0], swap_x_y=True, normalize=True, img_width=image.width, img_height=image.height, as_string=True)
                                    if output_minrect:
                                        bw, bh = polygon_to_minrect(poly[0])

                        # output
                        roi_file.write(
                            "{},{},{},{},{},{},{},{},{},{},{},{}".format(os.path.basename(im_name), x0, y0, x1, y1,
                                                                           x0n, y0n, x1n, y1n, label, label_str, score))
                        if output_polygons:
                            roi_file.write(',"{}","{}","{}","{}"'.format(",".join(px), ",".join(py), ",".join(pxn), ",".join(pyn)))
                            if output_minrect:
                                roi_file.write(',"{}","{}"'.format(bw, bh))
                        roi_file.write("\n")

                os.rename(roi_path_tmp, roi_path)
        except:
            print("Failed processing images: {}".format(",".join(im_list)))
            print(traceback.format_exc())

        # Move finished images to output_path or delete it
        for i in range(len(im_list)):
            if delete_input:
                os.remove(im_list[i])
            else:
                os.rename(im_list[i], os.path.join(output_dir, os.path.basename(im_list[i])))

        end_time = datetime.now()
        inference_time = end_time - start_time
        inference_time = int(inference_time.total_seconds() * 1000)
        time_per_image = int(inference_time / len(im_list))
        if inference_times:
            l = ""
            for i in range(len(im_list)):
                l += ("{}|".format(os.path.basename(im_list[i])))
            l += ",{},{},{}\n".format(inference_time, len(im_list), time_per_image)
            times.append(l)
        print("  Inference + I/O time: {} ms\n".format(inference_time))
        total_time += inference_time

        if inference_times:
            with open(os.path.join(output_dir, "inference_time.csv"), "w") as time_file:
                for l in times:
                    time_file.write(l)
            with open(os.path.join(output_dir, "total_time.txt"), "w") as total_time_file:
                total_time_file.write("Total inference and I/O time: {} ms\n".format(total_time))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--graph', help='Path to the frozen detection graph', required=True, default=None)
    parser.add_argument('--labels', help='Path to the labels map', required=True, default=None)
    parser.add_argument('--prediction_in', help='Path to the test images', required=True, default=None)
    parser.add_argument('--prediction_out', help='Path to the output csv files folder', required=True, default=None)
    parser.add_argument('--prediction_tmp', help='Path to the temporary csv files folder', required=False, default=None)
    parser.add_argument('--score', type=float, help='Score threshold to include in csv file', required=False, default=0.0)
    parser.add_argument('--output_polygons', action='store_true', help='Whether to masks are predicted and polygons should be output in the ROIS CSV files', required=False, default=False)
    parser.add_argument('--mask_threshold', type=float, help='The threshold (0-1) to use for determining the contour of a mask', required=False, default=0.1)
    parser.add_argument('--mask_nth', type=int, help='To speed polygon detection up, use every nth row and column only', required=False, default=1)
    parser.add_argument('--output_minrect', action='store_true', help='When outputting polygons whether to store the minimal rectangle around the objects in the CSV files as well', required=False, default=False)
    parser.add_argument('--num_classes', type=int, help='Number of classes', required=True, default=2)
    parser.add_argument('--num_imgs', type=int, help='Number of images to combine', required=False, default=1)
    parser.add_argument('--status', help='file path for predict exit status file', required=False, default=None)
    parser.add_argument('--continuous', action='store_true', help='Whether to continuously load test images and perform prediction', required=False, default=False)
    parser.add_argument('--output_inference_time', action='store_true', help='Whether to output a CSV file with inference times in the --prediction_output directory', required=False, default=False)
    parser.add_argument('--delete_input', action='store_true', help='Whether to delete the input images rather than move them to --prediction_out directory', required=False, default=False)
    parser.add_argument('--memory_fraction', type=float, help='Memory fraction to use by tensorflow', required=False, default=0.5)
    parsed = parser.parse_args()

    try:
        # Path to frozen detection graph. This is the actual model that is used for the object detection
        detection_graph = load_frozen_graph(parsed.graph)

        # Getting classes strings from the label map
        label_map = label_map_util.load_labelmap(parsed.labels)
        categories = label_map_util.convert_label_map_to_categories(label_map, max_num_classes=parsed.num_classes,
                                                                    use_display_name=True)

        with detection_graph.as_default():
            opts = tf.GPUOptions(per_process_gpu_memory_fraction=parsed.memory_fraction)
            with tf.compat.v1.Session(config=tf.ConfigProto(gpu_options=opts)) as sess:
                while True:
                    # Performing the prediction and producing the csv files
                    predict_on_images(parsed.prediction_in, detection_graph, sess, parsed.prediction_out, parsed.prediction_tmp,
                                      parsed.score, categories, parsed.num_imgs, parsed.output_inference_time,
                                      parsed.delete_input, parsed.output_polygons, parsed.mask_threshold,
                                      parsed.mask_nth, parsed.output_minrect)

                    # Exit if not continuous
                    if not parsed.continuous:
                        break
                if parsed.status is not None:
                    with open(parsed.status, 'w') as f:
                        f.write("Success")

    except Exception as e:
        print(traceback.format_exc())
        if parsed.status is not None:
            with open(parsed.status, 'w') as f:
                f.write(str(e))
