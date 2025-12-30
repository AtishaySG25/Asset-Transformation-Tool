from psd_tools import PSDImage
from src.psd_parser import extract_design_objects, merge_objects
from src.saliency import extract_salient_objects

psd_path = "smart_asset_resizer/input/Axis_Multicap_fund.psd"

psd = PSDImage.open(psd_path)

psd_objects = extract_design_objects(psd_path)
cv_objects = extract_salient_objects(psd)

objects = merge_objects(psd_objects, cv_objects)