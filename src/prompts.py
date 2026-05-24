from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


# Uses $var syntax (string.Template) instead of {var} (str.format) so prompt
# files can contain literal {...} JSON examples without escaping every brace.
def load_prompt(name: str, **vars) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    if vars:
        text = Template(text).safe_substitute(**vars)
    return text
