# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for webot Desktop.

Build: pyinstaller build.spec
Output: dist/webot.exe
"""
import sys
import site
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH)


# ── LangChain dynamically-imported submodules ─────────────────────────
def _collect(package: str) -> list[str]:
    """Collect submodules, tolerating optional dependencies."""
    try:
        from PyInstaller.utils.hooks import collect_submodules
        return collect_submodules(package, on_error="ignore")
    except Exception:
        return []

# ── Resolve webview runtime DLLs dynamically ───────────────────────────
def _find_webview_runtime_dir():
    """Find the webview package's runtime directory in site-packages."""
    for sp in site.getsitepackages():
        candidate = Path(sp) / "webview" / "lib"
        if candidate.exists():
            return candidate
    # Fallback: try user site-packages
    user_sp = site.getusersitepackages()
    if user_sp:
        candidate = Path(user_sp) / "webview" / "lib"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "webview package not found. Install with: pip install pywebview"
    )

_webview_dir = _find_webview_runtime_dir()
_webview_runtime = _webview_dir / "runtimes" / "win-x64" / "native" / "WebView2Loader.dll"
_webview_interop = _webview_dir / "WebBrowserInterop.x64.dll"

if not _webview_runtime.exists():
    raise FileNotFoundError(f"WebView2Loader.dll not found at {_webview_runtime}")
if not _webview_interop.exists():
    raise FileNotFoundError(f"WebBrowserInterop.x64.dll not found at {_webview_interop}")

a = Analysis(
    ['desktop.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=[
        (str(_webview_runtime), './runtimes/win-x64/native'),
        (str(_webview_interop), './lib'),
        (str(PROJECT_ROOT / 'native' / 'windows' / 'wcdb_api.dll'), 'native/windows'),
        (str(PROJECT_ROOT / 'native' / 'windows' / 'WCDB.dll'), 'native/windows'),
        (str(PROJECT_ROOT / 'native' / 'windows' / 'MSVCP140.dll'), 'native/windows'),
        (str(PROJECT_ROOT / 'native' / 'windows' / 'VCRUNTIME140.dll'), 'native/windows'),
        (str(PROJECT_ROOT / 'native' / 'windows' / 'VCRUNTIME140_1.dll'), 'native/windows'),
        (str(PROJECT_ROOT / 'native' / 'windows' / 'wx_key.dll'), 'native/windows'),
    ],
    datas=[
        ('ui/dist', 'ui/dist'),
        ('.env.example', '.'),
        # data/ is runtime-generated — do NOT bundle into read-only _MEIPASS
    ],
    hiddenimports=[
        'src', 'src.bot', 'src.config', 'src.main',
        'src.db', 'src.db.schema', 'src.db.store',
        'src.trigger', 'src.trigger.detector',
        'src.summarize', 'src.summarize.base', 'src.summarize.claude_backend',
        'src.summarize.deepseek_backend', 'src.summarize.models', 'src.summarize.prompts',
        'src.proactive', 'src.proactive.gate', 'src.proactive.modes',
        'src.proactive.rate_tracker', 'src.proactive.sticky',
        'src.memory', 'src.memory.consolidator', 'src.memory.conversation',
        'src.integrations', 'src.integrations.feishu',
        'src.integrations.feishu.client', 'src.integrations.feishu.exporter',
        'src.integrations.feishu.knowledge',
        'src.wechat', 'src.wechat.base', 'src.wechat.wcdb_backend',
        'src.wechat.wcdb_client', 'src.wechat.mac_hybrid_backend',
        'src.wechat.mac_ui_backend', 'src.wechat.window_controller',
        'src.wechat.keyboard', 'src.wechat.helpers', 'src.wechat.extract_key',
        'src.wechat.inactive_kick',
        'src.wechat.native', 'src.wechat.native.injector',
        'src.web', 'src.web.server',
        'src.router', 'src.welcome',
        'src.agent', 'src.agent.personas', 'src.agent.tools',
        'src.agent.factory', 'src.agent.runner',
        'src.game_utils',
        'src.koi', 'src.koi.rules', 'src.koi.signs', 'src.koi.bazi',
        'src.koi.store', 'src.koi.service', 'src.koi.handler',
        'src.nickname', 'src.admin',
        'src.utils', 'src.utils.logging_config',
        'dotenv', 'anthropic', 'openai', 'pydantic',
        'uiautomation',
        'webview', 'webview.platforms', 'webview.platforms.edgechromium',
        'PIL', 'PIL.Image', 'PIL.ImageDraw',
        'psutil', 'pyperclip',
        'win32api', 'comtypes',
        # LangChain / LangGraph agent stack.  These packages import many
        # submodules dynamically, so collect them explicitly.
        'langchain', 'langchain.agents', 'langchain.agents.middleware',
        'langchain.chat_models', 'langchain_core', 'langchain_core.tools',
        'langchain_core.messages', 'langchain_core.language_models',
        'langchain_openai', 'langchain_anthropic',
        'langgraph', 'langgraph.prebuilt', 'langgraph.checkpoint',
        'langgraph.checkpoint.memory', 'langgraph.store.memory',
        'tiktoken', 'tiktoken_ext', 'tiktoken_ext.openai_public',
        'jsonpatch', 'jsonpointer', 'tenacity', 'yaml',
    ] + _collect('langchain') + _collect('langchain_core')
      + _collect('langchain_openai') + _collect('langchain_anthropic')
      + _collect('langgraph') + _collect('tiktoken_ext'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'jedi', 'IPython',
              'faster_whisper', 'ctranslate2', 'numpy', 'onnxruntime',
              'pysilk', 'av', 'sounddevice', 'soundfile',
              # Exclude packages that are NOT webot dependencies but may
              # be installed in the local Python environment.  Bundling
              # them bloats the EXE by hundreds of MB.
              'torch', 'torchvision', 'transformers', 'pandas',
              'cv2', 'sklearn', 'ultralytics', 'joblib'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='webot',
    icon='image/logo_assets/logo.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
