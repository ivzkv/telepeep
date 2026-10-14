from telepeep import media
from telepeep.config import Config
from telepeep.project import Access


def test_safe_ext():
    assert media.safe_ext(".JPEG", None) == ".jpg"
    assert media.safe_ext(None, "application/pdf") == ".pdf"
    assert media.safe_ext(".exe", None) == ".bin"
    assert media.safe_ext("./../x", None) == ".bin"
    assert media.safe_ext(None, None) == ".bin"


def test_target_path_is_generated():
    assert media.target_path("work", -1001, 42, ".pdf").as_posix() == ".telepeep/out/work/-1001/42.pdf"


def test_project_can_only_lower_the_limit():
    cfg = Config(max_media_mb=50)
    assert media.limit_mb(cfg, Access()) == 50
    assert media.limit_mb(cfg, Access(max_media_mb=10)) == 10
    assert media.limit_mb(cfg, Access(max_media_mb=500)) == 50
