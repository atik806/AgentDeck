"""Offline tests for voice_postprocess.apply.

    .venv\\Scripts\\python.exe test_voice_postprocess.py
"""

import sys

import voice_postprocess

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


ON = {"voice_post_processing": True}
OFF = {"voice_post_processing": False}

print("[1] capitalisation + trailing-period trim on a single phrase")
check("caps first letter", voice_postprocess.apply("hello world", ON) == "Hello world")
check("drops whisper's trailing period",
      voice_postprocess.apply("git status.", ON) == "Git status")
check("already-capital left alone",
      voice_postprocess.apply("Git status", ON) == "Git status")

print("[2] multi-sentence punctuation is preserved")
check("internal period keeps final period",
      voice_postprocess.apply("First do this. Then do that.", ON)
      == "First do this. Then do that.")

print("[3] whitespace + edge cases")
check("collapses runs of whitespace",
      voice_postprocess.apply("run   the    thing", ON) == "Run the thing")
check("empty stays empty", voice_postprocess.apply("   ", ON) == "")
check("None -> ''", voice_postprocess.apply(None, ON) == "")
check("leading digit not altered",
      voice_postprocess.apply("3 files changed", ON) == "3 files changed")
check("ellipsis not stripped",
      voice_postprocess.apply("wait for it...", ON) == "Wait for it...")

print("[4] idempotence")
once = voice_postprocess.apply("deploy the app.", ON)
check("apply(apply(x)) == apply(x)", voice_postprocess.apply(once, ON) == once)

print("[5] disabled -> only strips outer whitespace")
check("no caps when off", voice_postprocess.apply("  hello world.  ", OFF) == "hello world.")

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
