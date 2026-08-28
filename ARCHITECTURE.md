# Voice-to-Text Desktop Application -- Architecture Document

> **Project:** Real-time voice-to-text transcription desktop application  
> **Stack:** Python, PyAudio, webrtcvad, Whisper.cpp (via pywhispercpp), PySide6  
> **Date:** 2026-07-09  
> **Status:** Design Phase  

---

## Table of Contents

1. [Overall Architecture](#1-overall-architecture)
2. [Directory Structure](#2-directory-structure)
3. [Data Classes and Types](#3-data-classes-and-types)
4. [Threading Model](#4-threading-model)
5. [Real-time Pipeline Design](#5-real-time-pipeline-design)
6. [Error Handling](#6-error-handling)
7. [Configuration](#7-configuration)
8. [Startup Sequence](#8-startup-sequence)
9. [Module Interface Contracts](#9-module-interface-contracts)
10. [Sequence Diagrams](#10-sequence-diagrams)

---

## 1. Overall Architecture

### 1.1 High-Level Block Diagram

```
                           voice_capture Package

  ┌──────────┐     ┌──────────────┐     ┌──────────────┐     ┌───────────┐
  │  Audio    │────▶│   Ring       │────▶│   VAD        │────▶│  Speech   │
  │  Capture  │     │   Buffer     │     │   (webrtcvad)│     │  Segment  │
  │  (Thread) │     │  (numpy circ)│     │              │     │  Queue    │
  └──────────┘     └──────────────┘     └──────────────┘     └─────┬─────┘
       ▲                                                           │
       │ audio thread writes chunks                                 │
       │                                                           ▼
  ┌────┴──────┐                                            ┌──────────────┐
  │  PyAudio  │                                            │ Transcriber  │
  │  (portaudio)                                           │ (Thread)     │
  └───────────┘                                            │              │
                                                           │ Whisper.cpp  │
                                                           │ inference    │
                                                           └──────┬───────┘
                                                                  │
                                                                  ▼
                                                           ┌──────────────┐
                                                           │ Result Queue │
                                                           │ (via Signal) │
                                                           └──────┬───────┘
                                                                  │
                                ┌─────────────────────────────────┘
                                ▼
                     ┌──────────────────────┐
                     │  Pipeline            │
                     │  (Orchestrator)      │
                     │  runs on main thread │
                     │  + QTimer poll       │
                     └──────┬───────────────┘
                            │ signal/slot
                            ▼
                     ┌──────────────────────┐
                     │  MainWindow (PySide6)│
                     │  - Start/Stop button  │
                     │  - Text display       │
                     │  - Status bar         │
                     └──────────────────────┘
```

### 1.2 Component Responsibilities

| Component | Responsibility | Runs On |
|-----------|---------------|---------|
| **Audio Capture** | Opens mic via PyAudio, reads frames in blocking loop, writes to ring buffer | Thread-2 (worker) |
| **VAD** | Consumes frames from ring buffer, detects speech/silence, manages utterance state machine | Main thread (QTimer poll) |
| **Transcriber** | Polls speech segment queue, invokes Whisper.cpp inference, emits results | Thread-3 (worker) |
| **Pipeline** | Orchestrates start/stop lifecycle, bridges worker results to GUI via Qt signals | Main thread (QTimer poll) |
| **MainWindow** | PySide6 UI: start/stop button, scrolling text area, status indicator | Main thread (GUI event loop) |

### 1.3 Module Dependency Graph

```
main.py
  └── app/pipeline.py
        ├── app/audio_capture.py
        ├── app/vad.py
        ├── app/transcriber.py
        └── app/ui/main_window.py
              └── app/config.py
```

### 1.4 Framework Choices

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Audio I/O | **PyAudio** (portaudio bindings) | Cross-platform, low-latency, ubiquitous |
| VAD | **webrtcvad** (WebRTC VAD) | Gold standard, 16 kHz, configurable aggressiveness |
| Inference | **Whisper.cpp** via pywhispercpp | Fast CPU inference, tiny/base for near-real-time |
| GUI | **PySide6** (Qt6) | Modern, stable, proper threading via signals/slots |
| Config | **JSON** via pathlib | Simple, human-readable, no extra deps |
| Packaging | **setuptools** (pyproject.toml) | Standard Python packaging |

---

## 2. Directory Structure

```
voice_capture/
├── main.py                          # Entry point, startup sequence
├── app/
│   ├── __init__.py
│   ├── audio_capture.py             # PyAudio stream + ring buffer
│   ├── vad.py                       # WebRTC VAD state machine
│   ├── transcriber.py               # Whisper.cpp inference wrapper
│   ├── pipeline.py                  # Orchestrator
│   ├── config.py                    # Load/save JSON config
│   ├── exceptions.py                # Custom exception classes
│   ├── types.py                     # Data classes
│   └── ui/
│       ├── __init__.py
│       ├── main_window.py           # PySide6 QMainWindow
│       └── styles.py                # QSS stylesheets
├── tests/
│   ├── __init__.py
│   ├── test_audio_capture.py
│   ├── test_vad.py
│   ├── test_transcriber.py
│   ├── test_pipeline.py
│   └── test_integration.py
├── models/                          # Whisper.cpp model files
│   └── .gitkeep
├── config.json
├── requirements.txt
├── pyproject.toml
├── README.md
└── ARCHITECTURE.md                  # This document
```

### 2.1 File Responsibilities

| File | Purpose |
|------|---------|
| `main.py` | Parse CLI args, load config, check deps, init pipeline, show GUI, exec app |
| `app/audio_capture.py` | `AudioCapture` class: open/close PyAudio stream, ring buffer, start/stop |
| `app/vad.py` | `VoiceActivityDetector` class: frame-by-frame VAD, utterance state machine |
| `app/transcriber.py` | `Transcriber` class: load Whisper model, transcribe float32 arrays, emit results |
| `app/pipeline.py` | `Pipeline` class: QObject with signals, owns all threads, coordinates lifecycle |
| `app/config.py` | `AppConfig` dataclass + load/save helpers |
| `app/types.py` | Shared data classes and enums |
| `app/exceptions.py` | Custom exception hierarchy |
| `app/ui/main_window.py` | `MainWindow`: QMainWindow with start/stop toggle, QTextEdit, status bar |
| `app/ui/styles.py` | QSS string constant for dark/light theming |

---

## 3. Data Classes and Types

### 3.1 `app/types.py` -- All Shared Types

```python
from __future__ import annotations
import enum
import time
from dataclasses import dataclass, field
from typing import Optional
import numpy as np


class PipelineState(enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    PROCESSING = "processing"
    ERROR = "error"


class VadState(enum.Enum):
    SILENCE = "silence"
    SPEECH = "speech"
    FLUSHING = "flushing"


@dataclass
class AudioSegment:
    samples: np.ndarray                 # float32, [-1, 1], 16 kHz mono
    sample_rate: int = 16000
    duration_seconds: float = field(init=False)
    segment_id: int
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        self.duration_seconds = len(self.samples) / self.sample_rate


@dataclass
class TranscriptionResult:
    text: str
    segment_id: int
    confidence: float
    language: str = "en"
    no_speech_prob: float = 0.0
    timestamps: list[tuple[float, float, str]] = field(default_factory=list)
    inference_duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class PipelineMetrics:
    audio_latency_ms: float = 0.0
    vad_queue_depth: int = 0
    last_inference_ms: float = 0.0
    avg_inference_ms: float = 0.0
    uptime_seconds: float = 0.0
    segments_transcribed: int = 0
    dropped_segments: int = 0


@dataclass
class AppConfig:
    mic_device_index: Optional[int] = None
    sample_rate: int = 16000
    channels: int = 1
    frames_per_buffer: int = 480
    vad_aggressiveness: int = 2
    vad_frame_ms: int = 30
    speech_padding_frames: int = 10
    min_speech_duration_ms: int = 200
    max_speech_duration_ms: int = 30_000
    silence_duration_ms: int = 500
    model_size: str = "base"
    model_path: Optional[str] = None
    language: str = "en"
    compute_device: str = "cpu"
    num_threads: int = 4
    word_timestamps: bool = False
    config_path: str = ""
```

---

## 4. Threading Model

### 4.1 Thread Diagram

```
  MAIN THREAD              WORKER THREAD 1            WORKER THREAD 2
  "Qt GUI Thread"          "Audio Capture Thread"     "Transcribe Thread"

  QApplication             AudioCapture               Transcriber
  MainWindow                 ._run()                    ._run()
  Pipeline (QObject)
                            ┌─────────────────┐       ┌──────────────────┐
  [QTimer 30ms] ────poll───▶ RingBuffer.read  │       │ queue.get()      │
  checks ring buf    │      │ all available    │       │ (blocking)       │
                     │      └────────┬─────────┘       └───────┬──────────┘
                     │               │                        │
  ┌───────────────┐  │               ▼                        ▼
  │ VAD.process() │◀──────── raw PCM frames         ┌──────────────────┐
  │(on main thread)│  │                              │ whisper.cpp      │
  └───────┬────────┘  │                              │ inference        │
          │            │                              └───────┬──────────┘
          ▼            │                                       │
  ┌──────────────┐     │                                       ▼
  │ Speech Deque │     │                               ┌──────────────────┐
  └──────┬───────┘     │                               │ emit             │
         │             │                               │ transcription    │
         ▼             │                               │ result via signal│
  ┌──────────────┐     │                               └────────┬─────────┘
  │ Drain to     │─────┼─────────────────────────────────────────▶
  │ Transcriber  │     │      put(AudioSegment)
  │ Queue        │     │
  └──────────────┘     │
         │             │
         ▼             │
  ┌──────────────┐     │
  │ Recv Result  │◀─────────────────────────────────────────────────
  │ via signal   │     │          transcription_result.emit(text)
  └──────┬───────┘     │
         │             │
         ▼             │
  ┌──────────────┐     │
  │ Update GUI   │     │
  └──────────────┘     │
```

### 4.2 Thread Responsibilities

**Thread 1: Main (GUI) Thread**
- Owns QApplication and MainWindow
- Owns Pipeline (a QObject)
- A QTimer at 30ms interval polls the ring buffer, runs VAD on accumulated frames
- When VAD produces a complete AudioSegment, pushes to transcriber_queue
- TranscriptionResult signals arrive on this thread (Qt::AutoConnection)
- All UI updates happen here

**Thread 2: Audio Capture Thread**
- Runs AudioCapture._run() loop
- Opens PyAudio stream in blocking read mode
- Reads frames in frames_per_buffer chunks (30ms)
- Writes raw 16-bit PCM into a lock-free ring buffer
- This is a QThread started/stopped by Pipeline

**Thread 3: Transcription Thread**
- Runs Transcriber._run() loop
- Blocks on queue.Queue.get()
- When AudioSegment arrives, runs Whisper.cpp inference
- Emits TranscriptionResult via Signal (thread-safe, Qt::AutoConnection)
- Can be QThread or threading.Thread; recommends QThread

### 4.3 Communication Primitives

| Primitive | Type | Direction | Content |
|-----------|------|-----------|---------|
| Ring Buffer | numpy circular buffer | Thread-2 → Main | Raw PCM int16 frames |
| Speech Queue | collections.deque (mutex) | Main thread internal | AudioSegment objects |
| Transcriber Queue | queue.Queue | Main → Thread-3 | AudioSegment objects |
| Result Signal | Signal(TranscriptionResult) | Thread-3 → Main | Transcribed text |
| State Signal | Signal(PipelineState) | Any → Main | State change notifications |
| Error Signal | Signal(str) | Any → Main | Error messages |

### 4.4 Shutdown Sequence

```
User clicks "Stop" or window closes:
  1. MainWindow -> pipeline.stop()
  2. Pipeline sets state -> STOPPING
  3. Pipeline calls audio_capture.stop()
  4. Thread-2: PyAudio stream closes, loop exits, thread joins
  5. Pipeline puts sentinel (None) into transcriber_queue
  6. Thread-3: receives None, exits loop, model unloaded, thread joins
  7. Pipeline flushes any remaining results
  8. Pipeline sets state -> IDLE
  9. UI re-enables start button
```

---

## 5. Real-time Pipeline Design

### 5.1 VAD State Machine

```
                    SILENCE
                       │
        speech detected│ (3 consecutive frames)
                       ▼
                   ┌──────┐
           ┌──────▶│ SPEECH│◀──────────────┐
           │       └──────┘                │
           │          │                    │
           │   silence detected            │
           │   (start counter)             │ VAD true during flush
           │          ▼                    │ (utterance continues)
           │       ┌──────────┐            │
           └───────│ FLUSHING │────────────┘
                   └──────────┘
                       │
          flush timeout│ (or max duration hit)
                       ▼
                    SILENCE (emit AudioSegment)
```

### 5.2 Audio Processing Pipeline (30ms Tick)

```
Every 30 ms (QTimer tick on main thread):

  1. READ:   ring_buf = audio_capture.ring_buffer.read_all()
             -> np.int16 array of newly available frames

  2. CONVERT: frames_f32 = ring_buf.astype(np.float32) / 32768.0

  3. VAD:    for each 30ms frame in ring_buf:
               is_speech = vad.is_speech(frame, sample_rate)
               action = vad_state_machine.update(is_speech)

  4. BUFFER: if state in (SPEECH, FLUSHING):
               append frames_f32 to utterance_buffer

  5. FLUSH:  if action == SPEECH_END:
               segment = AudioSegment(
                 samples=np.concatenate(utterance_buffer),
                 segment_id=next_id(),
               )
               transcriber_queue.put(segment)
               utterance_buffer.clear()
```

### 5.3 Latency Budget

| Stage | Target Latency | Notes |
|-------|---------------|-------|
| Audio capture chunk | 30 ms | frames_per_buffer=480 |
| Ring buffer read + VAD | ~0.5 ms | VAD is sub-millisecond |
| Speech detection debounce | ~300 ms | speech_padding_frames=10 |
| Queue transit | <1 ms | In-process queue |
| Whisper.cpp inference (base.en) | ~300-500 ms | On CPU, segments <10s |
| **Total from speech end to text** | **< 1 second** | Well within 2s budget |

---

## 6. Error Handling

### 6.1 Error Scenarios

| Scenario | Detection | Response |
|----------|-----------|----------|
| No microphone | PyAudio.open() raises IOError | Show dialog: "No input device found." |
| Mic disconnected mid-session | PyAudio read() error | Set ERROR state. Auto-retry every 2s. |
| Device busy | PyAudio.open() IOError | Show dialog: "Device busy by another app." |
| Model file not found | Transcriber checks path | Show dialog. Offer to download. |
| Whisper.cpp crash | Segfault or exception | Log error, emit signal, reload model, retry once. |
| Model load failure | whisper_init() returns null | Show dialog. Suggest re-download. |
| Permission denied (Linux) | PyAudio.open() OSError | Show dialog: "Add user to audio group." |
| Queue overflow | Speech queue > 50 | Drop oldest segment, log warning. |
| Config corruption | json.JSONDecodeError | Fall back to defaults, backup corrupted file. |

### 6.2 Exception Hierarchy

```python
class VoiceCaptureError(Exception): ...
class MicNotFoundError(VoiceCaptureError): ...
class DeviceBusyError(VoiceCaptureError): ...
class ModelNotFoundError(VoiceCaptureError): ...
class ModelLoadError(VoiceCaptureError): ...
class TranscriptionError(VoiceCaptureError): ...
class AudioPermissionError(VoiceCaptureError): ...
```

---

## 7. Configuration

### 7.1 Config File Location

```
Linux:   ~/.config/voice_capture/config.json
macOS:   ~/Library/Application Support/voice_capture/config.json
Windows: %APPDATA%\voice_capture\config.json
```

### 7.2 Example config.json

```json
{
  "mic_device_index": null,
  "sample_rate": 16000,
  "frames_per_buffer": 480,
  "vad_aggressiveness": 2,
  "vad_frame_ms": 30,
  "speech_padding_frames": 10,
  "min_speech_duration_ms": 200,
  "max_speech_duration_ms": 30000,
  "silence_duration_ms": 500,
  "model_size": "base",
  "model_path": null,
  "language": "en",
  "compute_device": "cpu",
  "num_threads": 4,
  "word_timestamps": false
}
```

---

## 8. Startup Sequence

```
main() entry point
│
├─ 1. Parse CLI args (--config, --model, --list-devices)
├─ 2. Check Python version (>=3.10)
├─ 3. Load config.json (merge with defaults; create if not exists)
├─ 4. Logging setup (~/.local/share/voice_capture/logs/)
├─ 5. Initialize QApplication (PySide6)
├─ 6. Model check:
│     a. Resolve model path
│     b. If not found -> show ModelDownloadDialog
│     c. Download from Hugging Face Hub
│     d. Validate file
├─ 7. Pre-load Whisper model in background thread
├─ 8. Enumerate audio devices:
│     a. List all input devices
│     b. Select configured or default
│     c. If none -> show error dialog
├─ 9. Construct Pipeline:
│     pipeline = Pipeline(config, model_path)
│     ├── Creates AudioCapture
│     ├── Creates Transcriber
│     └── Creates queues, connects signals
├─ 10. Construct MainWindow:
│      window = MainWindow(pipeline)
│      ├── Connect signals
│      ├── Show "Ready" state
│      └── window.show()
├─ 11. pipeline.initialize()
│      ├── Opens audio stream
│      └── Sets state -> IDLE
├─ 12. QApplication state: idle
└─ 13. app.exec() -- Enter Qt event loop
```

---

## 9. Module Interface Contracts

### 9.1 audio_capture.py -- AudioCapture

```python
class AudioCapture(QObject):
    error_occurred = Signal(str)

    def __init__(self, config: AppConfig, parent=None): ...
    def open(self) -> None:
        """Open PyAudio stream. Raises MicNotFoundError, DeviceBusyError."""
    def start(self) -> None:
        """Start capture in background QThread."""
    def stop(self) -> None:
        """Stop capture, join thread."""
    def close(self) -> None:
        """Close PyAudio stream, terminate PortAudio."""
    @staticmethod
    def list_devices() -> list[dict]:
        """Return list of available input devices."""
```

### 9.2 vad.py -- VoiceActivityDetector

```python
class VoiceActivityDetector:
    class Action(enum.Enum):
        NONE = 0
        SPEECH_START = 1
        SPEECH_CONTINUE = 2
        SPEECH_END = 3
        FORCE_FLUSH = 4

    def __init__(self, aggressiveness=2, sample_rate=16000, frame_ms=30,
                 padding_frames=10, max_duration_frames=1000,
                 min_duration_frames=7): ...
    def process_frame(self, frame: np.ndarray) -> Action: ...
    def reset(self) -> None: ...
```

### 9.3 transcriber.py -- Transcriber

```python
class Transcriber(QObject):
    result_ready = Signal(TranscriptionResult)
    error_occurred = Signal(str)
    model_loaded = Signal(bool)

    def __init__(self, config: AppConfig, parent=None): ...
    def load_model(self, model_path: str) -> bool: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def transcribe(self, segment: AudioSegment) -> None: ...
    @property
    def is_loaded(self) -> bool: ...
    @property
    def queue_size(self) -> int: ...
```

### 9.4 pipeline.py -- Pipeline

```python
class Pipeline(QObject):
    state_changed = Signal(PipelineState)
    transcription_result = Signal(TranscriptionResult)
    error_occurred = Signal(str)
    metrics_updated = Signal(PipelineMetrics)

    def __init__(self, config: AppConfig, parent=None): ...
    def initialize(self) -> None: ...
    def start_recording(self) -> None: ...
    def stop_recording(self) -> None: ...
    def shutdown(self) -> None: ...
    @property
    def state(self) -> PipelineState: ...
    @property
    def available_microphones(self) -> list[dict]: ...
```

### 9.5 main_window.py -- MainWindow

```
Layout:
 +----------------------------------------------------+
 | [Title Bar]              Settings    [--][ ][X]    |
 +----------------------------------------------------+
 |                                                    |
 |  +----------------------------------------------+  |
 |  |  Transcription Output (QTextEdit, read-only) |  |
 |  |                                              |  |
 |  |  > Hello this is a test of the               |  |
 |  |  > voice to text system it is working really |  |
 |  |  > well                                      |  |
 |  +----------------------------------------------+  |
 |                                                    |
 |  [Start/Stop]  Mic: [Built-in Audio Analog Stereo] |
 |  Status: Recording...  Segments: 12                |
 +----------------------------------------------------+

class MainWindow(QMainWindow):
    def __init__(self, pipeline: Pipeline): ...
    def connect_signals(self, pipeline: Pipeline) -> None: ...
    def append_transcription(self, result: TranscriptionResult) -> None: ...
    def update_state(self, state: PipelineState) -> None: ...
    def show_error_dialog(self, title: str, message: str) -> None: ...
```

---

## 10. Sequence Diagrams

### 10.1 Normal Recording Session

```
User        MainWindow     Pipeline      AudioCapture    Transcriber    Whisper
 |              |             |               |               |            |
 | click Start  |             |               |               |            |
 |------------->|             |               |               |            |
 |              | start_recording()          |               |            |
 |              |------------>|               |               |            |
 |              |             | state=RECORDING               |            |
 |              |             | start()       |               |            |
 |              |             |-------------->|               |            |
 |              |             |               | open stream   |            |
 |              |             |               | start loop    |            |
 |              |             | start()       |               |            |
 |              |             |------------------------------>|            |
 |              |             |               |               | wait on Q  |
 |              |             |               |               |            |
 |              |    +--------|               |               |            |
 |              |    | QTimer tick 30ms       |               |            |
 |              |    | read ring buf          |               |            |
 |              |    | VAD on frames          |               |            |
 |              |    | if speech: queue       |               |            |
 |              |    +--------|               |               |            |
 |              |             | put(segment)  |               |            |
 |              |             |-------------->|               |            |
 |              |             |               | infer()       |            |
 |              |             |               |-------------->|            |
 |              |             |               |               |            |
 |              |             |               |<--------------|            |
 |              |             | emit result   |               |            |
 |              |             |<--------------|               |            |
 |  see text    | append_text |               |               |            |
 |<-------------|<------------|               |               |            |
 |              |             |               |               |            |
 | click Stop   |             |               |               |            |
 |------------->|             |               |               |            |
 |              | stop_recording()            |               |            |
 |              |------------>|               |               |            |
 |              |             | stop()        |               |            |
 |              |             |-------------->|               |            |
 |              |             |               | close stream  |            |
 |              |             | stop() (sentinel)             |            |
 |              |             |------------------------------>|            |
 |              |             |               |               | join thread|
 |              |             | state=IDLE    |               |            |
 |<-------------|<------------|               |               |            |
```

### 10.2 Mic Disconnect Recovery

```
Pipeline              AudioCapture               MainWindow
   |                       |                         |
   | read() returns error  |                         |
   |<----------------------|                         |
   |                       |                         |
   | state=ERROR           |                         |
   | emit error_occurred() |                         |
   |----------------------------------------------->| show toast
   |                       |                         |
   | retry timer (2s)      |                         |
   | try reopen()          |                         |
   |---------------------->|                         |
   |                       | open new stream         |
   |<----------------------|                         |
   |                       |                         |
   | state=RECORDING       |                         |
   | emit "Mic reconnected"--> UI shows success      |
```

### 10.3 Startup with Model Download

```
main()            Transcriber         HuggingFace Hub       MainWindow
  |                   |                     |                    |
  | load_model("base")|                     |                    |
  |------------------>|                     |                    |
  |                   | model exists?       |                    |
  |                   |-- No                |                    |
  |                   |                     |                    |
  | model_not_found   |                     |                    |
  |---------------------------------------->| show download dialog
  |<----------------------------------------|                    |
  |                   |                     |                    |
  | download_model()  |                     |                    |
  |------------------>|                     |                    |
  |                   | GET ggml-base.bin   |                    |
  |                   |-------------------->|                    |
  |                   |<--------------------|                    |
  |                   |-- Save to models/   |                    |
  |                   |                     |                    |
  | model_loaded(True)|                     |                    |
  |---------------------------------------->| close dialog      |
  |                   |                     | show "Ready"      |
```

---

## Appendix A: Key Design Decisions

### A.1 Why QThread vs threading.Thread?
**Decision:** Use QThread. Integrates natively with Qt signals/slots. Auto-delivery to main thread via Qt::AutoConnection.

### A.2 Why VAD on Main Thread?
**Decision:** VAD runs on main thread in QTimer tick. WebRTC VAD is ~0.5ms per 30ms frame. Avoids extra thread/queue. Keeps audio thread minimal.

### A.3 Why Blocking PyAudio Read vs Callback?
**Decision:** Blocking read in Python thread. Callback runs in C real-time thread where GIL causes xruns. Blocking read with 30ms chunks is reliable.

### A.4 Model Download Strategy
**Decision:** Use huggingface_hub to download GGUF models from ggerganov/whisper.cpp. Files: ggml-tiny.bin (75MB), ggml-base.bin (142MB).

---

## Appendix B: Dependencies

### requirements.txt
```
PySide6>=6.6.0
PyAudio>=0.2.14
webrtcvad>=2.0.10
numpy>=1.26.0
pywhispercpp>=1.10.0
huggingface-hub>=0.23.0
sounddevice>=0.4.6
```

### System Dependencies (Linux)
```bash
# Debian/Ubuntu
sudo apt install portaudio19-dev python3-pyaudio
# Fedora
sudo dnf install portaudio-devel python3-pyaudio
# Arch
sudo pacman -S portaudio python-pyaudio
```

---

## Appendix C: Glossary

| Term | Definition |
|------|------------|
| VAD | Voice Activity Detection -- determines if an audio frame contains speech |
| WebRTC VAD | Google's VAD algorithm, lightweight and effective |
| Whisper.cpp | C++ implementation of OpenAI's Whisper speech-to-text |
| GGUF | Binary format for quantized ML models (llama.cpp ecosystem) |
| Ring Buffer | Fixed-size circular buffer for lock-free data transfer |
| Utterance | A continuous segment of speech between silence periods |
| Sentinel | Special value placed in a queue to signal shutdown |
| PyAudio | Python bindings for PortAudio |
| PortAudio | C library for cross-platform audio capture |
