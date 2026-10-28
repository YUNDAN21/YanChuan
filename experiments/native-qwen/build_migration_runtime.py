"""Build pinned CrispASR with a small, versioned LiveCaptions C ABI extension."""

import argparse
import os
import subprocess
from pathlib import Path

from build_tools import visual_studio_root

COMMIT = "e58bb48b52cf1fe957c19586297c83a2ed0d9fe4"


def change(path, before, after):
    text = path.read_text("utf-8")
    if after and after in text:
        return
    if not after and before not in text:
        return
    if text.count(before) != 1:
        raise ValueError(f"Upstream source no longer matches: {path}: {before[:70]}")
    path.write_text(text.replace(before, after), "utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vs-root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    work = root / "work/native-migration"
    source = work / "CrispASR"
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != COMMIT:
        raise ValueError("Requires CrispASR v0.8.45")
    session = source / "src/crispasr_c_api.cpp"
    change(
        session,
        "    std::string ask;",
        "    std::string ask;\n    std::string lc_context;\n    std::string lc_language;",
    )
    change(
        session,
        "std::string text = core_qwen3_prompt::build(N_enc, sys_instruction, s->ask, assistant_prefill,",
        "sys_instruction = s->lc_context.empty() ? sys_instruction : s->lc_context;\n"
        "        s->lc_language = lang_set ? lang : s->source_language;\n"
        "        std::string text = core_qwen3_prompt::build(N_enc, sys_instruction, s->ask, assistant_prefill,",
    )
    # Preserve the original decoded text. The generic session parser drops occurrences
    # of the ordinary word "language" and only consumes one token of language names.
    change(session, "        bool capture_language = false;", "        std::string lc_decoded;")
    change(
        session,
        "            std::string raw = raw_piece;\n            // Skip qwen3",
        "            std::string raw = raw_piece;\n"
        "            lc_decoded += gpt2_byte_decode(raw);\n            // Skip qwen3",
    )
    change(
        session,
        """            if (!raon && piece == "language") {
                capture_language = true;
                continue;
            }
            if (capture_language) {
                capture_language = false; // language name eaten, no transcript contribution
                continue;
            }
""",
        "",
    )
    change(
        session,
        """        while (!transcript.empty() && (transcript.front() == ' ' || transcript.front() == '\\n'))
            transcript.erase(transcript.begin());

        crispasr_session_seg seg;
""",
        """        if (!raon) {
            const auto tag = lc_decoded.find("<asr_text>");
            if (tag != std::string::npos) {
                auto prefix = lc_decoded.substr(0, tag);
                const auto language = prefix.find("language ");
                if (language != std::string::npos)
                    s->lc_language = prefix.substr(language + 9);
                transcript = lc_decoded.substr(tag + 10);
            }
        }
        while (!transcript.empty() && (transcript.front() == ' ' || transcript.front() == '\\n'))
            transcript.erase(transcript.begin());

        crispasr_session_seg seg;
""",
    )
    change(
        session,
        "        seg.text = core_ngram::fix_loops(transcript);",
        "        seg.text = transcript; // Preserve verbatim Qwen output; grouping belongs to the caller.",
    )
    marker = "\n// LiveCaptions ABI v1: no C++ structs cross this extension boundary.\n"
    text = session.read_text("utf-8")
    if marker not in text:
        text += (
            marker
            + r"""
CA_EXPORT int lc_qwen_abi_version() { return 1; }
CA_EXPORT int lc_qwen_open_params_size() { return sizeof(crispasr_open_params_v1); }
CA_EXPORT int lc_qwen_set_context(crispasr_session* s, const char* text) {
    if (!s || s->backend != "qwen3") return -1;
    s->lc_context = text ? text : "";
    return 0;
}
CA_EXPORT const char* lc_qwen_language(crispasr_session* s) {
    return s ? s->lc_language.c_str() : "";
}
CA_EXPORT const char* lc_qwen_device(crispasr_session* s) {
#ifdef CA_HAVE_QWEN3
    return s && s->qwen3_ctx ? lc_qwen_backend_name(s->qwen3_ctx) : "";
#else
    return "";
#endif
}
"""
        )
        session.write_text(text, "utf-8")
    change(
        session,
        "CA_EXPORT int lc_qwen_abi_version() { return 1; }",
        """CA_EXPORT int lc_qwen_abi_version() { return 1; }
CA_EXPORT int lc_qwen_set_greedy(crispasr_session* s, int max_tokens) {
    if (!s || s->backend != "qwen3" || max_tokens < 1) return -1;
    s->temperature = 0;
    s->seed = 0;
    s->beam_size = 1;
    s->best_of = 1;
    s->max_new_tokens = max_tokens;
    return 0;
}""",
    )
    header = source / "src/qwen3_asr.h"
    change(
        header,
        "// ---- KV-cache LLM API",
        "const char* lc_qwen_backend_name(struct qwen3_asr_context* ctx);\n\n// ---- KV-cache LLM API",
    )
    qwen = source / "src/qwen3_asr.cpp"
    loader = source / "src/core/gguf_loader.cpp"
    change(
        loader,
        "        HANDLE hFile = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);",
        """        const int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, nullptr, 0);
        if (length == 0) return;
        std::vector<wchar_t> wide_path(length);
        if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide_path.data(), length)) return;
        HANDLE hFile = CreateFileW(wide_path.data(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);""",
    )
    loader_text = loader.read_text("utf-8")
    loader.write_text(
        loader_text.replace('FILE* fp = fopen(path, "rb");', 'FILE* fp = ggml_fopen(path, "rb");'),
        "utf-8",
    )
    text = qwen.read_text("utf-8")
    if "const char* lc_qwen_backend_name(" not in text:
        text += '\nconst char* lc_qwen_backend_name(qwen3_asr_context* ctx) {\n    return ctx && ctx->backend ? ggml_backend_name(ctx->backend) : "";\n}\n'
        qwen.write_text(text, "utf-8")
    tools = visual_studio_root(args.vs_root)
    cmake_root = tools / "Common7/IDE/CommonExtensions/Microsoft/CMake"
    sdk = root / "work/native-qwen/sdk"
    vcvars = tools / "VC/Auxiliary/Build/vcvars64.bat"
    raw = subprocess.check_output(f'cmd.exe /d /s /c "call "{vcvars}" >nul && set"', text=True)
    environment = dict(os.environ)
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            environment[key] = value
    environment["VULKAN_SDK"] = str(sdk)
    environment["PATH"] = str(sdk / "Bin") + os.pathsep + environment["PATH"]
    cmake = str(cmake_root / "CMake/bin/cmake.exe")
    build = work / "build-vulkan"
    commands = [
        [
            cmake,
            "-S",
            str(source),
            "-B",
            str(build),
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DBUILD_SHARED_LIBS=ON",
            "-DGGML_VULKAN=ON",
            "-DGGML_CUDA=OFF",
            "-DGGML_NATIVE=OFF",
            "-DCRISPASR_PORTABLE_CPU=ON",
            "-DCRISPASR_BUILD_TESTS=OFF",
            "-DCRISPASR_BUILD_EXAMPLES=OFF",
            "-DCRISPASR_BUILD_SERVER=OFF",
            "-DCRISPASR_CURL=OFF",
            "-DCRISPASR_C2PA_FETCH=OFF",
            "-DCMAKE_PREFIX_PATH=" + str(sdk),
            "-DCMAKE_MAKE_PROGRAM=" + str(cmake_root / "Ninja/ninja.exe"),
        ],
        [cmake, "--build", str(build), "--target", "crispasr-lib", "-j", "4"],
    ]
    for name, command in zip(("configure", "build"), commands, strict=True):
        print(name, flush=True)
        with (work / (name + ".log")).open("w", encoding="utf-8") as log:
            subprocess.run(
                command, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True
            )


if __name__ == "__main__":
    main()
