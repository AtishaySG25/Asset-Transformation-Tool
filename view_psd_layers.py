from psd_tools import PSDImage

# Open the PSD file
psd = PSDImage.open(r"D:\Asset-Transformation-Tool\input\womensday.psd")

# Function to print the layers as a visual tree structure
def print_layer_tree(layer_or_group, depth=0):
    # Create indentation based on how deep the layer is nested
    indent = "  " * depth
    
    # Check if the item is a layer group
    if layer_or_group.kind == 'group':
        print(f"{indent}📁 [Group] {layer_or_group.name}")
        # Recursively loop through layers inside this group
        for child in layer_or_group:
            print_layer_tree(child, depth + 1)
    else:
        print(f"{indent}📄 [Layer] {layer_or_group.name}")

# Print the tree structure starting from the root file level
print("--- PSD Layer Tree Structure ---")
for layer in psd:
    print_layer_tree(layer)

print("\n--- Flat List of All Layers ---")
# This part still works perfectly for a quick flat list
for layer in psd.descendants():
    print(layer.name)
