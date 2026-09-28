"""只包含辅助对象，不收集测试用例。"""

import pytest

pytest.register_assert_rewrite(
    "tests.support.abaqus",
    "tests.support.web",
    "tests.support.web_solver",
    "tests.support.web_streaming",
)
