from PIL import Image, ImageDraw

def create_icon(size, filename):
    img = Image.new('RGB', (size, size), color = (73, 109, 237)) # Telegram Blue-ish
    d = ImageDraw.Draw(img)
    d.text((size//4, size//4), "T", fill=(255, 255, 255))
    img.save(filename)

create_icon(16, "c:/telegramhunter/chrome_extension/icons/icon16.png")
create_icon(48, "c:/telegramhunter/chrome_extension/icons/icon48.png")
create_icon(128, "c:/telegramhunter/chrome_extension/icons/icon128.png")
print("Icons created successfully.")
