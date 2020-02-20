# Training with Object Detection framework

Allows processing of images with Tensorflow's Object Detection framework, using Tensorflow 1.15.2.

## Version

Object Detection framework github repo hash:

```
b9ef963d1e84da0bb9c0a6039457c39a699ea149
```

and timestamp:

```
Fri Aug 30 14:39:49 2019 -0700
```

## Docker

### Build local image

* Build the image from Docker file (from within /path_to/tensorflow/object_detection/1.15.2_2019-08-31/train)

  ```commandline
  docker build -t tf_train .
  ```

* Run the container

  ```commandline
  docker run --runtime=nvidia -ti -v \
    /path_to/local_disk/containing_data:/path_to/mount/inside/docker_container tf_train \
    --pipeline_config_path=/path_to/your_data.config \
    --model_dir=/path_to/your_data/output --num_train_steps=50000 \
    --sample_1_of_n_eval_examples=1 --alsologtostderr
  ```

## Pre-built images

* Build

  ```commandline
  docker build -t tensorflow/object_detection:1.15.2_2019-08-31_train .
  ```
  
* Tag

  ```commandline
  docker tag \
    tensorflow/object_detection:1.15.2_2019-08-31_train \
    public-push.aml-repo.cms.waikato.ac.nz:443/tensorflow/object_detection:1.15.2_2019-08-31_train
  ```
  
* Push

  ```commandline
  docker push public-push.aml-repo.cms.waikato.ac.nz:443/tensorflow/object_detection:1.15.2_2019-08-31_train
  ```
  If error "no basic auth credentials" occurs, then run (enter username/password when prompted):
  
  ```commandline
  docker login public-push.aml-repo.cms.waikato.ac.nz:443
  ```
  
* Pull

  If image is available in aml-repo and you just want to use it, you can pull using following command and then [run](#run).

  ```commandline
  docker pull public.aml-repo.cms.waikato.ac.nz:443/tensorflow/object_detection:1.15.2_2019-08-31_train
  ```
  If error "no basic auth credentials" occurs, then run (enter username/password when prompted):
  
  ```commandline
  docker login public.aml-repo.cms.waikato.ac.nz:443
  ```
  Then tag by running:
  
  ```commandline
  docker tag \
    public.aml-repo.cms.waikato.ac.nz:443/tensorflow/object_detection:1.15.2_2019-08-31_train \
    tensorflow/object_detection:1.15.2_2019-08-31_train
  ```

* <a name="run">Run</a>

  ```commandline
  docker run --runtime=nvidia -v /local:/container -it tensorflow/object_detection:1.15.2_2019-08-31_train \
    --pipeline_config_path=/path_to/your_data.config \
    --model_dir=/path_to/your_data/output --num_train_steps=50000 \
    --sample_1_of_n_eval_examples=1 --alsologtostderr
  ```
  "/local:/container" maps a local disk directory into a directory inside the container
