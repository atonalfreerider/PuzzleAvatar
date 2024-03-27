import argparse
import os
import sys
import random
from PIL import Image
from glob import glob

import torch

sys.path.insert(0, os.path.join(sys.path[0], 'thirdparties/GroundingDINO'))

import base64
import json
import io
from typing import List

import cv2
import numpy as np
import requests
from groundingdino.util.inference import Model
from segment_anything import SamPredictor, sam_model_registry
from tqdm.auto import tqdm
from scipy import ndimage


def get_face(image):

    def bbox2(img):
        # from https://stackoverflow.com/a/31402351/19249364
        rows = np.any(img, axis=1)
        cols = np.any(img, axis=0)
        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]
        return rmin, rmax, cmin, cmax
    
    bbox = bbox2(image[:,:,0])
    cropped = image[bbox[0]:bbox[1]+1, bbox[2]:bbox[3]+1, :]
    
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cropped.copy())
    
    # STEP 2: Create an FaceDetector object.
    base_options = python.BaseOptions(model_asset_path="./multi_concepts/blaze_face_short_range.tflite")
    options = vision.FaceDetectorOptions(base_options=base_options)
    detector = vision.FaceDetector.create_from_options(options)
    
    # STEP 4: Detect faces in the input image.
    try:
        face_score = detector.detect(mp_image).detections[0].categories[0].score
        return face_score > 0.60
    except:
        return False



def enhance_class_name(class_names: List[str]) -> List[str]:

    new_class_names = []
    for class_name in class_names:
        if class_name == 'face':
            new_class_names.append('face')
        else:
            new_class_names.append(f"all {class_name}")

    return new_class_names


# Function to encode the image
def encode_image(image_path, res=(600, 800)):
    buffer = io.BytesIO()
    img = Image.open(image_path).resize(res).convert('RGB')
    img.save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def segment(sam_predictor: SamPredictor, image: np.ndarray, xyxy: np.ndarray) -> np.ndarray:
    sam_predictor.set_image(image)
    result_masks = []
    for box in xyxy:
        masks, scores, logits = sam_predictor.predict(box=box, multimask_output=True)
        index = np.argmax(scores)
        result_masks.append(masks[index])
    return np.array(result_masks)


def resizeAndPad(img, size, padColor=0):

    h, w = img.shape[:2]
    sh, sw = size

    # interpolation method
    if h > sh or w > sw:    # shrinking image
        interp = cv2.INTER_AREA
    else:    # stretching image
        interp = cv2.INTER_CUBIC

    # aspect ratio of image
    aspect = w / h    # if on Python 2, you might need to cast as a float: float(w)/h

    # compute scaling and pad sizing
    if aspect > 1:    # horizontal image
        new_w = sw
        new_h = np.round(new_w / aspect).astype(int)
        pad_vert = (sh - new_h) / 2
        pad_top, pad_bot = np.floor(pad_vert).astype(int), np.ceil(pad_vert).astype(int)
        pad_left, pad_right = 0, 0
    elif aspect < 1:    # vertical image
        new_h = sh
        new_w = np.round(new_h * aspect).astype(int)
        pad_horz = (sw - new_w) / 2
        pad_left, pad_right = np.floor(pad_horz).astype(int), np.ceil(pad_horz).astype(int)
        pad_top, pad_bot = 0, 0
    else:    # square image
        new_h, new_w = sh, sw
        pad_left, pad_right, pad_top, pad_bot = 0, 0, 0, 0

    # set pad color
    if len(img.shape) == 3 and not isinstance(padColor, (list, tuple, np.ndarray)):
        # color image but only one color provided
        padColor = [padColor] * 3

    # scale and pad
    scaled_img = cv2.resize(img, (new_w, new_h), interpolation=interp)
    scaled_img = cv2.copyMakeBorder(
        scaled_img,
        pad_top,
        pad_bot,
        pad_left,
        pad_right,
        borderType=cv2.BORDER_CONSTANT,
        value=padColor
    )

    return scaled_img


def gpt4v_captioning(img_dir):

    headers = {
        "Content-Type": "application/json", "Authorization":
        f"Bearer {os.environ['OPENAI_API_KEY']}"
    }

    if "PuzzleIOI" in img_dir:
        used_lst = np.random.choice(glob(f"{img_dir}/*_raw.jpg"), 2)
        used_lst = [os.path.basename(img) for img in used_lst]
        prompt = open("./multi_concepts/gpt4v_simple.txt", "r").read()
        res = (600, 800)
        # used_lst = [f"{idx}.jpg" for idx in np.random.randint(101, 120, 3)]
    elif "thuman2" in img_dir:
        used_lst = ["000.png", "180.png"]
        prompt = open("./multi_concepts/gpt4v_complex.txt", "r").read()
        res = (256, 256)
    else:
        used_lst = random.sample(os.listdir(img_dir), 3)
    images = [encode_image(os.path.join(img_dir, img_name), res=res) for img_name in used_lst]

    payload = {
        "model": "gpt-4-vision-preview", "messages":
        [{"role": "user", "content": [
            {"type": "text", "text": prompt},
        ]}], "max_tokens": 500
    }
    for image in images:
        payload["messages"][0]["content"].append({
            "type": "image_url", "image_url":
            {"url": f"data:image/jpeg;base64,{image}", "detail": "low"}
        })

    response = requests.post(
        "https://api.openai.com/v1/chat/completions", headers=headers, json=payload
    )

    result = response.json()['choices'][0]['message']['content']

    return result


def face_asset_combine(mask, mask_final, CLASSES, struct, labels_add, labels_remove):

    for label in labels_add:
        if label in CLASSES and CLASSES.index(label) in mask_final.keys():
            mask = ndimage.binary_dilation(
                np.logical_or(mask, mask_final[CLASSES.index(label)]),
                structure=struct,
                iterations=3
            )
            print(f"add {label} {mask_final.keys()}")
    for label in labels_remove:
        if label in CLASSES and CLASSES.index(label) in mask_final.keys():
            mask = ndimage.binary_erosion(
                np.logical_and(mask, 1.0 - mask_final[CLASSES.index(label)]),
                structure=struct,
                iterations=3
            )
            print(f"remove {label} {mask_final.keys()}")

    return mask


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--in_dir', type=str, required=True, help="input image folder")
    parser.add_argument('--out_dir', type=str, required=True, help="output mask folder")
    parser.add_argument('--overwrite', action="store_true")
    opt = parser.parse_args()

    # gpt_filename = "gpt4v_complex.json"
    gpt_filename = "gpt4v_simple.json"

    if not os.path.exists(f"{opt.out_dir}/mask"):
        os.makedirs(f"{opt.out_dir}/mask", exist_ok=True)

    if opt.overwrite:
        for f in os.listdir(f"{opt.out_dir}/mask"):
            os.remove(os.path.join(f"{opt.out_dir}/mask", f))

    DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # paths
    GroundingDINO_dir = "thirdparties/GroundingDINO"
    GROUNDING_DINO_CONFIG_PATH = os.path.join(
        GroundingDINO_dir, "groundingdino/config/GroundingDINO_SwinT_OGC.py"
    )
    GROUNDING_DINO_CHECKPOINT_PATH = os.path.join(
        GroundingDINO_dir, "weights/groundingdino_swint_ogc.pth"
    )
    SAM_CHECKPOINT_PATH = os.path.join(GroundingDINO_dir, "weights/sam_vit_h_4b8939.pth")
    SAM_ENCODER_VERSION = "vit_h"

    # load models
    grounding_dino_model = Model(
        model_config_path=GROUNDING_DINO_CONFIG_PATH,
        model_checkpoint_path=GROUNDING_DINO_CHECKPOINT_PATH
    )
    sam = sam_model_registry[SAM_ENCODER_VERSION](checkpoint=SAM_CHECKPOINT_PATH).to(device=DEVICE)
    sam_predictor = SamPredictor(sam)

    BOX_TRESHOLD = 0.30
    TEXT_TRESHOLD = 0.40

    try:
        json_path = f"{opt.out_dir}/{gpt_filename}"

        if not os.path.exists(json_path):
            gpt4v_response = gpt4v_captioning(os.path.join(opt.in_dir, "image"))
            with open(json_path, "w") as f:
                f.write(gpt4v_response)
        else:
            with open(json_path, "r") as f:
                gpt4v_response = f.read()
                

        print(gpt4v_response)

        CLASSES = [item.strip() for item in json.loads(gpt4v_response).keys() if item != 'gender']
        CLASSES = ["person"] + CLASSES

        print(CLASSES)

    except Exception as e:
        print(e)
        with open("./clusters/error.txt", "a") as f:
            f.write(f"{opt.in_dir[5:]} {' '.join(opt.in_dir.split('/')[-2:])}\n")
        if os.path.exists(f"{opt.in_dir}/{gpt_filename}"):
            os.remove(f"{opt.in_dir}/{gpt_filename}")
        sys.exit()

    for img_name in tqdm(os.listdir(opt.in_dir + "/image")):

        if "raw" not in img_name:

            img_path = os.path.join(opt.in_dir, "image", img_name)

            image = cv2.imread(img_path)
            if image is not None:
                if image.shape[:2] != (4096, 4096):
                    image = resizeAndPad(image, (4096, 4096))
                    cv2.imwrite(img_path, image)

                # detect objects
                detections = grounding_dino_model.predict_with_classes(
                    image=image,
                    classes=enhance_class_name(class_names=CLASSES),
                    box_threshold=BOX_TRESHOLD,
                    text_threshold=TEXT_TRESHOLD
                )

                # convert detections to masks
                detections.mask = segment(
                    sam_predictor=sam_predictor,
                    image=cv2.cvtColor(image, cv2.COLOR_BGR2RGB),
                    xyxy=detections.xyxy
                )

                mask_dict = {}

                print(img_name, detections.class_id, CLASSES)

                # if there is person in the image
                if 0 in detections.class_id:
                    person_masks = detections.mask[detections.class_id == 0]
                    person_mask = (np.stack(person_masks).sum(axis=0) > 0).astype(np.uint8)

                    for mask, cls_id in zip(detections.mask, detections.class_id):
                        if cls_id is not None and cls_id != 0:
                            if np.logical_and(mask, person_mask).sum() / person_mask.sum() < 0.9:
                                mask_dict[cls_id] = mask_dict.get(cls_id, []) + [mask]

                    mask_final = {}

                    # stack all the masks of the same class together within the same image
                    for cls_id, masks in mask_dict.items():
                        mask = np.stack(masks).sum(axis=0) > 0
                        mask_final[cls_id] = mask

                    # remove the overlapping area
                    for cls_id, mask in mask_final.items():

                        if "face" in CLASSES and cls_id == CLASSES.index("face"):

                            mask = face_asset_combine(
                                mask,
                                mask_final,
                                CLASSES,
                                np.ones((3, 3)),
                                ["eyeglasses", "glasses"],
                                ["haircut", "hair"],
                            )

                            mask_final[cls_id] = mask

                        else:
                            mask_other = np.zeros_like(mask)
                            other_cls_ids = list(mask_final.keys())
                            other_cls_ids.remove(cls_id)
                            for other_cls_id in other_cls_ids:
                                mask_other += mask_final[other_cls_id]
                            mask_final[cls_id] = mask * (mask_other == 0)

                        if (mask_final[cls_id]).sum() > 500:
                            if CLASSES[cls_id] not in ["eyeglasses", "glasses"]:
                                
                                keep = True
                                
                                if CLASSES[cls_id] == "face":
                                    face_img = image * mask_final[cls_id][:, :, None]
                                    keep = get_face(face_img)  
                                
                                if keep:
                                    cv2.imwrite(
                                        f"{opt.out_dir}/mask/{img_name[:-4]}_{CLASSES[cls_id]}.png",
                                        mask_final[cls_id].astype(np.uint8) * 255
                                    )
