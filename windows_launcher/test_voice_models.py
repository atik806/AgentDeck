"""Offline tests for voice_models (model recommendation + resolution).

    .venv\\Scripts\\python.exe test_voice_models.py
"""

import sys

import voice_models

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


_REGISTRY = set(voice_models._registry())

print("[1] recommend_model picks a real English model per tier")
orig_ram = voice_models._total_ram_gb
orig_cpu = voice_models.os.cpu_count
try:
    voice_models._total_ram_gb = lambda: 4.0
    voice_models.os.cpu_count = lambda: 8
    check("4 GB -> base.en", voice_models.recommend_model() == "base.en")

    voice_models._total_ram_gb = lambda: 32.0
    voice_models.os.cpu_count = lambda: 2
    check("2 cores -> base.en", voice_models.recommend_model() == "base.en")

    voice_models._total_ram_gb = lambda: 16.0
    voice_models.os.cpu_count = lambda: 8
    check("16 GB / 8 cores -> small.en", voice_models.recommend_model() == "small.en")

    voice_models._total_ram_gb = lambda: 0.0
    check("unknown RAM -> fallback base.en", voice_models.recommend_model() == "base.en")
    check("recommendation is always a registry name",
          voice_models.recommend_model() in _REGISTRY)
finally:
    voice_models._total_ram_gb = orig_ram
    voice_models.os.cpu_count = orig_cpu

print("[2] resolve")
check("'auto' -> a registry name", voice_models.resolve("auto") in _REGISTRY)
check("known name passes through", voice_models.resolve("small.en") == "small.en")
check("garbage -> fallback", voice_models.resolve("banana") == "base.en")
check("None -> a registry name", voice_models.resolve(None) in _REGISTRY)
check("whitespace tolerated", voice_models.resolve("  base.en ") == "base.en")

print("[3] labels + prompt")
check("every label key except 'auto' resolves",
      all(voice_models.resolve(k) in _REGISTRY
          for k in voice_models.MODEL_LABELS if k != "auto"))
check("DEFAULT_PROMPT mentions git", "git" in voice_models.DEFAULT_PROMPT.lower())

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
