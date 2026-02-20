import re
import os

file_path = r'c:\telegramhunter\app\workers\tasks\flow_tasks.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add the async_execute helper
helper = '''logger = logging.getLogger("flow.tasks")

# Helper for async DB execution
async def async_execute(query_builder):
    """Executes a Supabase query builder synchronously in a background thread."""
    return await asyncio.to_thread(query_builder.execute)
'''
content = content.replace('logger = logging.getLogger("flow.tasks")', helper)

def repl(match):
    inner = match.group(1)
    return f'await async_execute(db.table{inner})'

new_content = re.sub(r'db\.table((?:(?!db\.table).)*?)\.execute\(\)', repl, content, flags=re.DOTALL)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)
    
print("Remaining .execute():", new_content.count('.execute()'))
