import os

extension_path = r'c:\telegramhunter\app\services\scanners_extension.py'
main_path = r'c:\telegramhunter\app\services\scanners.py'

with open(extension_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Skip the first 10 lines (imports and logger)
content_to_append = "".join(lines[10:])

with open(main_path, 'a', encoding='utf-8') as f:
    f.write("\n\n" + content_to_append)

print("Successfully appended scanners to scanners.py")
