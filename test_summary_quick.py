from bot import _format_summary
from types import SimpleNamespace

fc = SimpleNamespace(
    xml_found=True,
    xml_version_ok=True,
    iul_present=False,
    missing_sections=[],
    issues=[SimpleNamespace(code="FC-001", message="Test", severity="warning")],
    warning_count=1,
    critical_count=0
)

pp963 = SimpleNamespace(
    tep_compliant=False,
    tep_discrepancies=[
        "Error code: 403 - {'error': {'message': 'Forbidden'}}"
    ],
    sections_checked=0,
    sections_passed=0,
    llm_model="gpt-oss-120b"
)

sv = SimpleNamespace(
    is_compliant=False,
    compliance_rate=0.9,
    total_items=178,
    compliant_count=169,
    non_compliant_count=6,
    skipped_count=3,
    items=[
        SimpleNamespace(requirement="Req 1", compliant=False),
        SimpleNamespace(requirement="Req 2", compliant=False)
    ],
    error=""
)

nr = SimpleNamespace(
    found=True,
    status="active",
    reg_number="P-118313",
    fio="Черных Игорь Вячеславович"
)

result = SimpleNamespace(
    task_id="000",
    status="done",
    formal_check=fc,
    pp963_report=pp963,
    sverka_check=sv,
    nopriz_check=nr,
    files=[],
    total_files=208,
    verdict="PENDING_EXPERT",
    verdict_reason="Test reason"
)

try:
    summary = _format_summary(result, "Test Package", 10.0)
    print("SUCCESS, length:", len(summary))
    print("="*40)
    print(summary)
except Exception as e:
    import traceback
    traceback.print_exc()
