import copy
import math
import re
from pathlib import Path
from typing import List, Tuple, Union
import matplotlib.pyplot as plt
import numpy as np
from pdf2image import convert_from_bytes, convert_from_path
from PIL import Image, ImageDraw, ImageFont
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
from Model.utils.textwrap_japanese import fw_fill_ja
from Model.utils.textwrap_vietnamese import fw_fill_vi
import torchvision
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor
from torchvision.models.detection import MaskRCNN_ResNet50_FPN_Weights
from torchvision.transforms import transforms
import torch
import random
import cv2
import os
import fitz
import easyocr
# from paddleocr import PaddleOCR
import time

from Backend.services.settings import MEDIA_ROOT

seed = 1234
random.seed(seed)
torch.manual_seed(seed)
torch.cuda.manual_seed_all(seed)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False

CATEGORIES2LABELS = {
    0: "bg",
    1: "text",
    2: "title",
    3: "list",
    4: "table",
    5: "figure"
}
MODEL_PATH = "D:/dev/translation_layoutrecovery/Backend/model_196000.pth"
def get_instance_segmentation_model(num_classes):
    '''
    This function returns a Mask R-CNN model with a ResNet-50-FPN backbone.
    The model is pretrained on the PubLayNet dataset. 
    -----
    Input:
        num_classes: number of classes
    Output:
        model: Mask R-CNN model with a ResNet-50-FPN backbone
    '''
    model = torchvision.models.detection.maskrcnn_resnet50_fpn(weights=MaskRCNN_ResNet50_FPN_Weights.DEFAULT)
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)
    in_features_mask = model.roi_heads.mask_predictor.conv5_mask.in_channels
    hidden_layer = 256

    model.roi_heads.mask_predictor = MaskRCNNPredictor(
        in_features_mask,
        hidden_layer,
        num_classes
    )
    return model

class TranslationLayoutRecovery:
    """TranslationLayoutRecovery class.

    Attributes from _load_init()
    ----------
    font: ImageFont
        Font for drawing text on the image
    ocr_model: EasyOCR
        OCR model for detecting text in the text blocks
    translate_model: 
        Translation model for translating text
    translate_tokenizer: 
        Tokenizer for decoding the output of the translation model
    """
    DPI = 300
    FONT_SIZE_VIETNAMESE = 34
    FONT_SIZE_JAPANESE = 28

    def __init__(self):
        self._load_init()

    def _repeated_substring(self, s: str):
        n = len(s)
        for i in range(10, n // 2 + 1):
            pattern = s[:i]
            matches = [match for match in re.finditer(rf'\b{re.escape(pattern)}\b', s)]
            if len(matches) >= 15:
                return True
        for i in range(n // 2 + 11, n):
            pattern = s[n // 2 + 1:i]
            matches = [match for match in re.finditer(rf'\b{re.escape(pattern)}\b', s)]
            if len(matches) >= 15:
                return True
        return False

    def translate_pdf(self, input_path: Union[Path, bytes], language: str, output_path: Path, merge: bool) -> None:
        """Backend function for translating PDF files."""
        pdf_images = convert_from_path(
            input_path, 
            dpi=self.DPI, 
            poppler_path=r"D:\dev\translation_layoutrecovery\Backend\poppler-24.08.0\Library\bin"
        )
        
        print("Language:", language)
        self.language = language
        pdf_files = []
        reached_references = False

        # Batch processing
        idx = 0
        file_id = 0
        batch_size = 8

        for _ in tqdm(range(math.ceil(len(pdf_images) / batch_size))):
            image_list = pdf_images[idx:idx + batch_size]
            if not reached_references:
                image_list, reached_references = self._translate_multiple_pages(
                    image_list=image_list,
                    reached_references=reached_references,
                )

                # Save translated pages to PDF files
                for translated_image, original_image in image_list:
                    saved_output_path = os.path.join(output_path, f"{file_id:03}.pdf")
                    with fitz.open() as pdf_writer:
                        pil_image = Image.fromarray(translated_image).convert("RGB")
                        pil_image.save(saved_output_path)
                        pdf_files.append(saved_output_path)
                    file_id += 1

            idx += batch_size

        # Merge all PDFs if required
        if merge:
            self._merge_pdfs(pdf_files)

    def _load_init(self):
        """Backend function for loading models.

        Called in the constructor.
        Load the layout model, OCR model, translation model and font.
        """
        self.font_ja = ImageFont.truetype(
           os.path.join(os.getcwd(), "Source Han Serif CN Light.otf"),
            size=self.FONT_SIZE_JAPANESE,
        )
        self.font_vi = ImageFont.truetype(
            os.path.join(os.getcwd(), "AlegreyaSans-Regular.otf"),
            size=self.FONT_SIZE_VIETNAMESE,
        )
        
        # Detection model: PubLayNet
        self.num_classes = len(CATEGORIES2LABELS.keys())
        self.pub_model = get_instance_segmentation_model(self.num_classes)

        if os.path.exists(MODEL_PATH):
            self.checkpoint_path = MODEL_PATH
        else:
            raise Exception("Model weights not found.")

        assert os.path.exists(self.checkpoint_path)
        checkpoint = torch.load(self.checkpoint_path, map_location='cuda:0')
        self.pub_model.load_state_dict(checkpoint['model'])
        self.pub_model = self.pub_model.to("cuda")
        self.pub_model.eval()

        # Recognition model: PaddleOCR
        # self.ocr_model = PaddleOCR(ocr=True, use_gpu=True, lang="en", ocr_version="PP-OCRv4")
        self.ocr_model = easyocr.Reader(['en'], gpu=True)
        
        # Translation model
        # self.translate_model_ja = AutoModelForSeq2SeqLM.from_pretrained("Helsinki-NLP/opus-mt-en-jap").to("cuda:0")
        # self.translate_tokenizer_ja = AutoTokenizer.from_pretrained("Helsinki-NLP/opus-mt-en-jap")
        
        self.translate_model_vi = AutoModelForSeq2SeqLM.from_pretrained("VietAI/envit5-translation").to("cuda:0")
        self.translate_tokenizer_vi = AutoTokenizer.from_pretrained("VietAI/envit5-translation")

        self.transform = transforms.Compose([
            transforms.ToPILImage(),
            transforms.ToTensor()
        ])

    def _crop_img(self, box, ori_img):
        new_box_0 = int(box[0] / self.rat) - 20
        new_box_1 = int(box[1] / self.rat) - 10
        new_box_2 = int(box[2] / self.rat) + 20
        new_box_3 = int(box[3] / self.rat) + 10
        temp_img = ori_img[new_box_1:new_box_3, new_box_0:new_box_2]
        box = [new_box_0, new_box_1, new_box_2, new_box_3]
        return temp_img, box

    def _ocr_module(self, list_boxes, list_labels_idx, ori_img):
        original_image = copy.deepcopy(ori_img)
        list_labels = list(map(lambda y: CATEGORIES2LABELS[y.item()], list_labels_idx))
        list_masks = list(map(lambda x: x == "text", list_labels))
        list_boxes_filtered = list_boxes[list_masks]
        list_images_filtered = [original_image] * len(list_boxes_filtered)

        results = list(map(self._crop_img, list_boxes_filtered, list_images_filtered))

        if len(results) > 0:
            list_temp_images, list_new_boxes = [row[0] for row in results], [row[1] for row in results]
            
            list_ocr_results = list(map(lambda x: np.array(x, dtype=object)[:, 1] if len(x) > 0 else None, 
                                        list(map(lambda x: self.ocr_model.readtext(x), list_temp_images))))

            for ocr_results, box in zip(list_ocr_results, list_new_boxes):
                if ocr_results is not None:
                    ocr_text = " ".join(ocr_results)
                    if len(ocr_text) > 1:
                        text = re.sub(r"\n|\t|\[|\]|\/|\|", " ", ocr_text)
                        translated_text = self._translate(text)
                        translated_text = re.sub(r"\n|\t|\[|\]|\/|\|", " ", translated_text)

                        # if most characters in translated text are not 
                        # japanese characters, skip
                        if self.language == "ja":
                            if len(
                                re.findall(
                                    r"[^\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBF]",
                                    translated_text,
                                )
                            ) > 0.8 * len(translated_text):
                                print("skipped")
                                continue
                        
                        # for VietAI/envit5-translation, replace "vi"
                        if self.language == "vi":
                            translated_text = translated_text.replace("vi: ", "")
                            translated_text = translated_text.replace("vi ", "")
                            translated_text = translated_text.strip()
                            
                        if self.language == "ja":
                            if self._repeated_substring(translated_text): # Check repeated substring
                                processed_text = fw_fill_ja(
                                    text,
                                    width=int(
                                        (box[2] - box[0]) / (self.FONT_SIZE_JAPANESE / 2)
                                    )
                                    + 1,
                                )
                            else:
                                processed_text = fw_fill_ja(
                                    translated_text,
                                    width=int(
                                        (box[2] - box[0]) / (self.FONT_SIZE_JAPANESE / 2)
                                    )
                                    + 1,
                                )
                        else:
                            if self._repeated_substring(translated_text):
                                processed_text = fw_fill_vi(
                                    text,
                                    width=int(
                                        (box[2] - box[0]) / (self.FONT_SIZE_VIETNAMESE / 2)
                                    )
                                    + 1,
                                )
                            else:
                                processed_text = fw_fill_vi(
                                    translated_text,
                                    width=int(
                                        (box[2] - box[0]) / (self.FONT_SIZE_VIETNAMESE / 2)
                                    )
                                    + 1,
                                )

                        new_block = Image.new(
                            "RGB",
                            (
                                box[2] - box[0],
                                box[3] - box[1],
                            ),
                            color=(255, 255, 255),
                        )
                        draw = ImageDraw.Draw(new_block)
                        if self.language == "ja":
                            draw.text(
                                (0, 0),
                                text=processed_text,
                                font=self.font_ja,
                                fill=(0, 0, 0),
                            )
                        else:
                            draw.text(
                                (0, 0),
                                text=processed_text,
                                font=self.font_vi,
                                fill=(0, 0, 0),
                            )
                        
                        new_block = np.array(new_block)
                        original_image[
                            int(box[1]) : int(box[3]),
                            int(box[0]) : int(box[2]),
                        ] = new_block
                else:
                    continue

        reached_references = False

        # Check title "Reference" or "References", if so then stop
        list_title_masks = list(map(lambda x: x == "title", list_labels))
        list_boxes_filtered = list_boxes[list_title_masks]
        list_images_filtered = [original_image] * len(list_boxes_filtered)

        results = list(map(self._crop_img, list_boxes_filtered, list_images_filtered))
        if len(results) > 0:
            list_temp_images = [row[0] for row in results]
            list_title_ocr_results = list(map(lambda x: np.array(x, dtype=object)[:, 1] if len(x) > 0 else None, 
                                        list(map(lambda x: self.ocr_model.readtext(x), list_temp_images))))
            if len(list_title_ocr_results) > 0:
                for i, (result, box) in enumerate(zip(list_title_ocr_results, list_boxes_filtered)):
                    if result is not None:
                        if result[0].lower() in ["references", "reference"]:
                            reached_references = True
                        elif result[0].lower() == "abstract":
                            # Use the original Title and Authors, skip translating them
                            new_box_1 = int(box[1] / self.rat)
                            original_image[
                                int(0) : int(new_box_1),
                                int(0) : int(original_image.shape[1]),
                            ] = ori_img[
                                int(0) : int(new_box_1),
                                int(0) : int(ori_img.shape[1]),
                            ]
                    
        return original_image, reached_references
    
    def _preprocess_image(self, image):
        ori_img = np.array(image)
        img = ori_img[:, :, ::-1].copy()
        
        # Get the ratio to resize
        self.rat = 1000 / img.shape[0]

        img = cv2.resize(img, None, fx=self.rat, fy=self.rat)
        img = self.transform(img).cuda()

        return [img, ori_img]
    
    def _translate_multiple_pages(
        self,
        image_list: List[Image.Image],
        reached_references: bool,
    ) -> Tuple[np.ndarray, np.ndarray, bool]:
        """Translate one page of the PDF file.

        There are some heuristics to clean-up the results of translation:
            1. Remove newlines, tabs, brackets, slashes, and pipes
            2. Reject the result if there are few Japanese characters
            3. Skip the translation if the text block has only one line

        Parameters
        ----------
        image_list: List[Image.Image]
            Image of the page
        reached_references: bool
            Whether the references section has been reached.

        Returns
        -------
        Tuple[np.ndarray, np.ndarray, bool]
            Translated image, original image,
            and whether the references section has been reached.
        """
        results = list(map(self._preprocess_image, image_list))
        new_list_images, list_original_images = [row[0] for row in results], [row[1] for row in results]
        with torch.no_grad():
            predictions = self.pub_model(new_list_images)

        list_masks = list(map(lambda x : x["scores"] >= 0.7, predictions))
        new_list_boxes = list(map(lambda x, y : x['boxes'][y,:], predictions, list_masks))
        new_list_labels = list(map(lambda x, y : x["labels"][y], predictions, list_masks))   

        list_returned_images = []
        reached_references = False
        for one_image_boxes, one_image_labels, original_image in zip(new_list_boxes, new_list_labels, 
                                                                                list_original_images):
            one_translated_image, reached_references = self._ocr_module(one_image_boxes, one_image_labels, original_image)
            list_returned_images.append([one_translated_image, original_image])
            if reached_references:
                break

        return list_returned_images, reached_references

    def _translate(self, text: str) -> str:
        """Translate the text in PDF files using 
        the translation model.

        If the text is too long, it will be splited by rule-based method
        so that each sentence should be within 450 characters.

        Parameters
        ----------
        text: str
            Text to be translated.

        Returns
        -------
        str
            Translated text.
        """
        texts = self._split_text(text, 450)

        translated_texts = []
        for i, t in enumerate(texts):
            http_res = ("http" in t) or ("https" in t)
            if not http_res:
                if self.language == "ja":
                    inputs = self.translate_tokenizer_ja(t, return_tensors="pt").input_ids.to(
                        "cuda"
                    )
                    outputs = self.translate_model_ja.generate(inputs, max_length=512)
                    res = self.translate_tokenizer_ja.decode(outputs[0], skip_special_tokens=True)
                else:
                    inputs = self.translate_tokenizer_vi(t, return_tensors="pt").input_ids.to(
                        "cuda"
                    )
                    outputs = self.translate_model_vi.generate(inputs, max_length=512)
                    res = self.translate_tokenizer_vi.decode(outputs[0], skip_special_tokens=True)
            else:
                res = t
            
            # skip translated text at first
            if self.language == "ja" and res.startswith("「この版"):
                continue

            translated_texts.append(res)
        return " ".join(translated_texts)

    def _split_text(self, text: str, text_limit_length: int = 448) -> List[str]:
        """Split text into chunks of sentences within text_limit_length.

        Parameters
        ----------
        text: str
            Text to be split.
        text_limit_length: int
            Maximum length of each chunk. Defaults to 448.

        Returns
        -------
        List[str]
            List of text chunks,
            each of which is shorter than text_limit_length.
        """
        if len(text) < text_limit_length:
            return [text]

        sentences = text.rstrip().split(". ")
        sentences = [s + ". " for s in sentences[:-1]] + sentences[-1:]
        result = []
        current_text = ""
        for sentence in sentences:
            if len(current_text) + len(sentence) < text_limit_length:
                current_text += sentence
            else:
                if current_text:
                    result.append(current_text)
                while len(sentence) >= text_limit_length:
                    result.append(sentence[:text_limit_length - 1])
                    sentence = sentence[text_limit_length - 1:].lstrip()
                current_text = sentence
        if current_text:
            result.append(current_text)
        return result

    def _merge_pdfs(self, pdf_files: List[str]) -> None:
        """Merge the translated PDF files into one file using fitz."""
        # Ensure the target directory exists
        output_dir = os.path.join(MEDIA_ROOT, "PDFs")
        os.makedirs(output_dir, exist_ok=True)  # Create the directory if it doesn't exist

        output_file = os.path.join(output_dir, "fitz_translated.pdf")
        
        result = fitz.open()
        for pdf_file in sorted(pdf_files):
            with fitz.open(pdf_file) as f:
                result.insert_pdf(f)
        result.save(output_file)
        result.close()
        print(f"Saved merged PDF to {output_file}")

if __name__ == "__main__":
    obj = TranslationLayoutRecovery()
    obj.translate_pdf(
        language="ja",
        input_path="1711.07064-1-4.pdf",
        output_path="outputs/",
        merge=False,
    )
