from pydantic import BaseModel, Field

class TestModel(BaseModel):
    name: str
    pdf_report: bytes | None = Field(default=None, exclude=True)

test_obj = TestModel(name="Test", pdf_report=b"hello_world")
print("Has pdf_report:", hasattr(test_obj, "pdf_report"))
print("Value:", getattr(test_obj, "pdf_report", None))

