import pytest

from ragnarok.harness.calc import try_calc
from ragnarok.router.intent_rules import match_intent


@pytest.mark.parametrize("text,expected", [
    ("calculate 18 percent GST on 42500", "18% GST on 42,500 is 7,650. Total with it: 50,150. Without it: 42,500."),
    ("what is 12 times 37", "444"),
    ("15% of 2000", "15% of 2,000 is 300."),
    ("what is 20 percent discount on 1,500", "20% discount on 1,500 is 300. You pay 1,200."),
    ("calculate (3 + 4) * 5", "35"),
    ("how much is 100 divided by 8", "12.5"),
    ("what's 2 to the power of 10", "1,024"),
    ("12 x 12", "144"),
])
def test_calculator(text, expected):
    assert try_calc(text) == expected


@pytest.mark.parametrize("text", ["what is the budget review about", "how much is invoice 8812", "what is 1234", "hello", "calculate", "what is the capital of france", "2026-10-25"])
def test_not_arithmetic(text):
    assert try_calc(text) is None


def test_no_code_execution_through_the_calculator():
    assert try_calc("calculate __import__('os').system('ls')") is None
    assert try_calc("calculate 9**9**9") is None


def test_run_python_is_routed_to_the_sandboxed_skill():
    assert match_intent("run python to print the sum of 1 to 100") == "python.run"
    assert match_intent("run the script that sums the column") == "python.run"
    assert match_intent("run a quick check") is None
