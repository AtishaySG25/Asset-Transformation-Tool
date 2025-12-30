from psd_tools import PSDImage

psd_name = "layer_"
psd = PSDImage.open("D:/Asset-Transformation-Tool/new_dimensions/Axis_Multicap_fund.psd")

x = 0

for layer in psd:
    x += 1

    if layer.kind == "smartobject":
        image = layer.composite()

        if image:
            filename = f"{psd_name}{x}.png"
            image.save(filename)
            print(f"Saved Smart Object layer {x} as {filename}")

for i, layer in enumerate(psd, start=1):
    print(i, layer.name, layer.kind, type(layer.kind))
