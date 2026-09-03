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
PUNCT = {"voice_post_processing": True, "voice_spoken_punctuation": True}
FIX = {"voice_post_processing": True, "voice_command_fixups": True}

print("[1] trailing-period trim on a single phrase, no auto-capitalise")
check("case is left alone (this feeds a shell)",
      voice_postprocess.apply("echo hello", ON) == "echo hello")
check("drops whisper's trailing period",
      voice_postprocess.apply("git status.", ON) == "git status")

print("[2] multi-sentence punctuation is preserved")
check("internal period keeps final period",
      voice_postprocess.apply("first do this. then do that.", ON)
      == "first do this. then do that.")

print("[3] whitespace + edge cases")
check("collapses runs of whitespace",
      voice_postprocess.apply("run   the    thing", ON) == "run the thing")
check("empty stays empty", voice_postprocess.apply("   ", ON) == "")
check("None -> ''", voice_postprocess.apply(None, ON) == "")
check("ellipsis not stripped",
      voice_postprocess.apply("wait for it...", ON) == "wait for it...")

print("[4] idempotence")
once = voice_postprocess.apply("deploy the app.", ON)
check("apply(apply(x)) == apply(x)", voice_postprocess.apply(once, ON) == once)

print("[5] disabled -> only strips outer whitespace")
check("passthrough when off", voice_postprocess.apply("  hello world.  ", OFF) == "hello world.")

print("[6] spoken punctuation (opt-in)")
check("period -> . with no leading space",
      voice_postprocess.apply("list files period", PUNCT) == "list files.")
check("comma spacing",
      voice_postprocess.apply("a comma b", PUNCT) == "a, b")
check("new line -> newline char",
      "\n" in voice_postprocess.apply("first line new line second", PUNCT))
check("off by default", voice_postprocess.apply("done period", ON) == "done period")
check("idempotent", voice_postprocess.apply(
    voice_postprocess.apply("x period", PUNCT), PUNCT) == "x.")

print("[7] command fixups (experimental, opt-in)")
check("get -> git at line start",
      voice_postprocess.apply("get commit", FIX) == "git commit")
check("pseudo -> sudo", voice_postprocess.apply("pseudo apt update", FIX) == "sudo apt update")
check("off by default", voice_postprocess.apply("get commit", ON) == "get commit")
check("get not touched mid-line",
      voice_postprocess.apply("dont forget commit", FIX) == "dont forget commit")

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
