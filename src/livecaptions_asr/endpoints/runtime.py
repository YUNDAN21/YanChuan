"""Explicit local ONNX loading; importing this module loads no model/runtime."""

from pathlib import Path

from ..domain import ASRError


def cpu_session(path: Path, threads: int):
    if not isinstance(threads, int) or isinstance(threads, bool) or threads < 1:
        raise ASRError("invalid_options", "CPU 推理线程数必须是正整数。")
    if not Path(path).is_file():
        raise ASRError("endpoint_model_missing", f"检测模型不存在：{path}")
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise ASRError("dependency_missing", "请安装 livecaptions-asr[endpoint]。") from exc
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
