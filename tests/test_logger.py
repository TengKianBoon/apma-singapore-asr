import json
from services.logger import get_logger, init_logging


def test_logger_emits_json_line(capsys):
    init_logging("INFO")
    logger = get_logger("test_logger", job_id="jid123")
    logger.info("hello world")
    captured = capsys.readouterr()
    out = captured.out.strip().splitlines()[-1]
    obj = json.loads(out)
    assert "ts" in obj
    assert obj["level"] == "INFO"
    assert obj["module"] == "test_logger"
    assert obj["message"] == "hello world"
    assert obj["job_id"] == "jid123"
    assert isinstance(obj["pid"], int)
