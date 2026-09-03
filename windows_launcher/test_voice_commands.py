"""Offline tests for voice_commands.parse.

    .venv\\Scripts\\python.exe test_voice_commands.py
"""

import sys

import voice_commands

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


ON = {"voice_commands_enabled": True}
OFF = {"voice_commands_enabled": False}


def act(text, cfg=ON):
    return voice_commands.parse(text, cfg)[0]


print("[1] each phrase maps to its action")
for phrase in ("send", "run that", "Run it.", "submit", "go ahead", "execute"):
    check(f"{phrase!r} -> submit", act(phrase) == "submit")
for phrase in ("new line", "newline", "line break"):
    check(f"{phrase!r} -> newline", act(phrase) == "newline")
for phrase in ("scratch that", "delete that", "undo that", "Erase that!"):
    check(f"{phrase!r} -> scratch", act(phrase) == "scratch")
for phrase in ("stop listening", "stop dictation", "never mind", "nevermind"):
    check(f"{phrase!r} -> stop", act(phrase) == "stop")

print("[2] a phrase is only a command when it's the WHOLE utterance")
check("'send the email' is literal text", act("send the email") is None)
check("'run that script now' is literal", act("run that script now") is None)
check("'please scratch that line' is literal", act("please scratch that line") is None)
a, rest = voice_commands.parse("echo hello world", ON)
check("plain dictation returns (None, original)", a is None and rest == "echo hello world")

print("[3] case / trailing punctuation / whitespace tolerated")
check("'SEND.' -> submit", act("SEND.") == "submit")
check("'  run it  ' -> submit", act("  run it  ") == "submit")
check("'Scratch that?' -> scratch", act("Scratch that?") == "scratch")

print("[4] disabled -> always (None, text)")
check("disabled: 'send' is text", voice_commands.parse("send", OFF) == (None, "send"))
check("empty -> (None, '')", voice_commands.parse("", ON)[0] is None)

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
