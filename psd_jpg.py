# from psd_tools import PSDImage

# psd = PSDImage.open('D:/Asset-Transformation-Tool/new_aspect_ratios/input/Axis.psd')

# for layer in psd:
#     print(f"Layer name: {layer.name}, Visible: {layer.visible}")
#     if layer.visible and layer.kind == 'pixel':
#         # Composite the individual layer image
#         layer_image = layer.composite() 
#         if layer_image:
#             layer_image.save(f'{layer.name}.png')

from psd_tools import PSDImage

# Open the PSD file
psd = PSDImage.open('D:/Asset-Transformation-Tool/new_aspect_ratios/input/Axis.psd')

# Composite all layers into a single flattened image (PIL Image object)
merged_image = psd.composite()

# Save the merged image as another format (e.g., PNG)
# The image format is automatically determined by the file extension
merged_image.save('D:/Asset-Transformation-Tool/new_aspect_ratios/input/Axis.png') 

print("Image created and saved as output_image.png")
