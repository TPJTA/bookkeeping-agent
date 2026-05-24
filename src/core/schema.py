from pydantic import BaseModel, Field, field_validator

VALID_CATEGORIES = {"餐饮", "交通", "购物", "娱乐", "生活缴费", "其他"}


class ModifyResult(BaseModel):
    """Output schema of prompts/modify.md."""
    is_modification: bool
    transaction: "Transaction"


class Transaction(BaseModel):
    is_transaction: bool
    merchant: str = ""
    goods: str = ""
    category: str = "其他"
    amount: str = ""
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("category")
    @classmethod
    def _check_category(cls, v: str) -> str:
        # Empty is legit for non-transaction images (prompt says "识别不到填 ''");
        # silently default to "其他" instead of erroring + retrying for nothing.
        if not v:
            return "其他"
        if v not in VALID_CATEGORIES:
            raise ValueError(
                f"category '{v}' must be one of {sorted(VALID_CATEGORIES)}"
            )
        return v

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, v: str) -> str:
        if v == "":
            return v
        try:
            float(v)
        except (TypeError, ValueError) as e:
            raise ValueError(f"amount '{v}' must be a numeric string") from e
        return v


ModifyResult.model_rebuild()
