"""Extended Java benchmark: 10 more tasks across SonarJava rule classes.

Tasks chosen to stress Kappa's structural rules vs Gemma's pattern knowledge:
  T01 DeadStore          S1854 value overwritten before read
  T02 ConditionalReturn  if/elif chains where naive return-scan kills live code
  T03 MultiResource      two leaks in one method (S2095 x2)
  T04 LoopLeak           resource opened inside loop body
  T05 DeadAfterGuard     early-return guard + dead tail
  T06 UnusedLocal        S1481 declared-never-read
  T07 UnusedParam        S1172 (expected unfixable-by-both: signature change)
  T08 ChainedCalls       System.out/qualified calls (Java builtins, no FP)
  T09 TryFinallyLeak     leak inside try with finally block
  T10 MultipleMethods    several methods, dead code only in one
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")
from llm.router import generate
import re

SRC = Path("/home/chaitanya/Projects/final-year-project/src")
SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
BENCH = SP / "java_bench2"
BENCH.mkdir(exist_ok=True)
JAR = SRC / "benchmark" / "sorald-0.8.6-jar-with-dependencies.jar"
MODEL = "google/gemma-4-31b-it-turbo"
RULES = ["S2095", "S1854", "S1481", "S1172", "S106", "S1488"]

FILES = {
    "T01_DeadStore": """public class T01_DeadStore {
    public int compute(int input) {
        int result = input * 3;
        result = input + 7;
        return result;
    }
}
""",
    "T02_ConditionalReturn": """public class T02_ConditionalReturn {
    public String grade(int score) {
        if (score >= 90) {
            return "A";
        }
        if (score >= 80) {
            return "B";
        }
        return "C";
        System.out.println("unreachable");
    }
}
""",
    "T03_MultiResource": """import java.io.FileInputStream;
import java.io.IOException;

public class T03_MultiResource {
    public int copy(String a, String b) {
        FileInputStream in = new FileInputStream(a);
        FileInputStream out = new FileInputStream(b);
        try {
            return in.read() + out.read();
        } catch (IOException e) {
            return 0;
        }
    }
}
""",
    "T04_LoopLeak": """import java.io.FileInputStream;
import java.io.IOException;

public class T04_LoopLeak {
    public int readFirstLine(String[] paths) {
        for (String path : paths) {
            FileInputStream fis = new FileInputStream(path);
            try {
                return fis.read();
            } catch (IOException e) {
                return 0;
            }
        }
        return -1;
    }
}
""",
    "T05_DeadAfterGuard": """public class T05_DeadAfterGuard {
    public int sign(int x) {
        if (x > 0) {
            return 1;
        }
        if (x < 0) {
            return -1;
        }
        return 0;
        System.out.println("done");
    }
}
""",
    "T06_UnusedLocal": """public class T06_UnusedLocal {
    public int calculate(int a, int b) {
        int unused = a * b;
        return a + b;
    }
}
""",
    "T07_UnusedParam": """public class T07_UnusedParam {
    public int add(int a, int b) {
        return a + b;
    }

    public int legacy(int a, int b, int flags) {
        return a + b;
    }
}
""",
    "T08_ChainedCalls": """public class T08_ChainedCalls {
    public void report(int value) {
        System.out.println("value=" + value);
        System.out.println(Math.abs(value));
    }
}
""",
    "T09_TryFinallyLeak": """import java.io.FileInputStream;
import java.io.IOException;

public class T09_TryFinallyLeak {
    public int read(String path) {
        FileInputStream fis = new FileInputStream(path);
        try {
            return fis.read();
        } catch (IOException e) {
            return 0;
        }
    }
}
""",
    "T10_MultipleMethods": """public class T10_MultipleMethods {
    public int alpha(int x) {
        return x * 2;
    }

    public int beta(int y) {
        return y + 1;
        System.out.println("dead in beta");
    }

    public int gamma(int z) {
        return z - 1;
    }
}
""",
}

print("=" * 64)
print("JAVA BENCH 2: 10 tasks — Kappa vs Gemma vs Union (Sorald oracle)")
print("=" * 64)


def mine(path: Path) -> tuple[int, dict]:
    r = subprocess.run(
        ["java", "-jar", str(JAR), "mine", "--source", str(path), "--rule-keys", ",".join(RULES)],
        capture_output=True, text=True, timeout=120,
    )
    counts, total = {}, 0
    for line in (r.stdout or "").splitlines():
        if "=" in line and not line.startswith("SLF4J"):
            key, val = line.strip().split("=", 1)
            n = int(val)
            if n > 0:
                counts[key] = n
                total += n
    return total, counts


def extract_code(response: str) -> str:
    text = re.sub(r"<(?:thought|think|thinking)>.*?</(?:thought|think|thinking)>", "", response, flags=re.DOTALL).strip()
    if "```" in text:
        chunks = []
        for chunk in text.split("```")[1::2]:
            code = chunk.strip()
            first, _, rest = code.partition("\n")
            if first.strip() in ("java", "Java"):
                code = rest
            if code.strip():
                chunks.append(code.strip())
        return chunks[-1] if chunks else ""
    return text.strip()


pairs = []
print("\n[step 1] staging 10 tasks, Sorald baseline")
for name, content in FILES.items():
    path = BENCH / f"{name}.java"
    path.write_text(content)
    before, per_rule = mine(path)
    pairs.append({"id": name, "file_path": str(path), "before": before})
    print(f"  {name:22s} {before} violations {per_rule if per_rule else ''}")

print("\n[step 2] Kappa (tree-sitter)")
kappa_dir = SP / "java2-kappa"
kappa_dir.mkdir(exist_ok=True)
for old in kappa_dir.glob("*.java"):
    old.unlink()
for pair in pairs:
    out_file = kappa_dir / f"{pair['id']}.java"
    subprocess.run(
        ["conda", "run", "-n", "benchmark", "python", "-m", "kappa",
         pair["file_path"], "--parser-backend", "tree-sitter",
         "--out", str(out_file), "--max-iters", "3"],
        capture_output=True, text=True, timeout=300, cwd=str(SRC),
    )
    k_after = mine(out_file)[0] if out_file.exists() else pair["before"]
    pair["kappa_after"] = k_after
    print(f"  {pair['id']:22s} {pair['before']} -> {k_after}")

print("\n[step 3] Gemma (RapidRouter)")
gemma_dir = SP / "java2-gemma"
gemma_dir.mkdir(exist_ok=True)
for old in gemma_dir.glob("*.java"):
    old.unlink()
for pair in pairs:
    code = Path(pair["file_path"]).read_text()
    prompt = (
        "The following Java class has SonarQube violations (resource leaks, unused "
        "assignments, unreachable code, unused variables, hardcoded secrets).\n"
        "Fix them with minimal changes. Keep the class logic identical. "
        "Return ONLY the corrected Java class in one ```java code block.\n\n"
        + code
    )
    started = time.perf_counter()
    try:
        response = generate(prompt, MODEL, temperature=0.0, max_tokens=1024)
        fixed = extract_code(response)
        if fixed.strip():
            gfile = gemma_dir / f"{pair['id']}.java"
            gfile.write_text(fixed)
            pair["gemma_after"] = mine(gfile)[0]
            print(f"  {pair['id']:22s} {pair['before']} -> {pair['gemma_after']}  ({time.perf_counter() - started:.1f}s)")
        else:
            pair["gemma_after"] = pair["before"]
            print(f"  {pair['id']:22s} EMPTY extraction")
    except Exception as exc:
        pair["gemma_after"] = pair["before"]
        print(f"  {pair['id']:22s} FAILED: {exc}")

print("\n" + "=" * 64)
print("SUMMARY")
print("=" * 64)
header = f"  {'task':22s} {'before':>6s} {'kappa':>6s} {'gemma':>6s} {'union':>6s}"
print(header)
rows = []
for pair in pairs:
    union_after = min(pair["kappa_after"], pair["gemma_after"])
    rows.append({k: pair[k] for k in ("id", "before", "kappa_after", "gemma_after")} | {"union_after": union_after})
    print(f"  {pair['id']:22s} {pair['before']:6d} {pair['kappa_after']:6d} {pair['gemma_after']:6d} {union_after:6d}")

kb = sum(r["before"] for r in rows)
ka = sum(r["kappa_after"] for r in rows)
ga = sum(r["gemma_after"] for r in rows)
ua = sum(r["union_after"] for r in rows)
print(f"\n  total violations : {kb}")
print(f"  kappa repaired   : {kb - ka} ({100 * (kb - ka) / max(kb, 1):.0f}%)  remaining={ka}")
print(f"  gemma repaired   : {kb - ga} ({100 * (kb - ga) / max(kb, 1):.0f}%)  remaining={ga}")
print(f"  UNION repaired   : {kb - ua} ({100 * (kb - ua) / max(kb, 1):.0f}%)  remaining={ua}")
(SP / "java2-summary.json").write_text(json.dumps(rows, indent=2) + "\n")
print(f"\ndetails -> {SP / 'java2-summary.json'}")
