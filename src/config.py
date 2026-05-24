import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


@dataclass(frozen=True)
class Config:
    lark_app_id: str
    lark_app_secret: str
    # BITABLE_APP_TOKEN may hold a wiki node token when the table lives in a
    # 知识库; storage layer resolves it to the real obj_token at startup.
    bitable_app_token: str
    bitable_table_id: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str


CONFIG = Config(
    lark_app_id=_required("LARK_APP_ID"),
    lark_app_secret=_required("LARK_APP_SECRET"),
    bitable_app_token=_required("BITABLE_APP_TOKEN"),
    bitable_table_id=_required("BITABLE_TABLE_ID"),
    llm_api_key=_required("MODAL_API_KEY"),
    llm_base_url=_required("MODAL_APP_BASE"),
    llm_model=_required("MODAL_APP_NAME"),
)
