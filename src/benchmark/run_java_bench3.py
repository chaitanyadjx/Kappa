"""Java bench 3: 12 harder tasks. Goal: find where Kappa still misses, then
reconfigure Kappa (detector/patcher) accordingly. Rules under test:
  S2095 resource leak | S1854 dead store | S1481 unused local
  S106 System.out     | S1488 expression value ignore | S3984 object alloc unused
  S1854-in-loop       | dead store across call boundary | self-assignment
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")

SRC = Path("/home/chaitanya/Projects/final-year-project/src")
SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
BENCH = SP / "java_bench3"
BENCH.mkdir(exist_ok=True)
JAR = SRC / "benchmark" / "sorald-0.8.6-jar-with-dependencies.jar"
MODEL = "google/gemma-4-31b-it-turbo"
RULES = ["S2095", "S1854", "S1481", "S1172", "S106", "S1488", "S3984", "S4165"]

FILES = {
    # --- new rule classes ---
    "U01_UnusedObject": """public class U01_UnusedObject {
    public void handle(String input) {
        new StringBuilder(input).reverse();
    }
}
""",
    "U02_IgnoredReturnValue": """public class U02_IgnoredReturnValue {
    public String clean(String raw) {
        raw.trim();
        return raw;
    }
}
""",
    "U03_SelfAssignment": """public class U03_SelfAssignment {
    private int count;

    public void reset(int count) {
        count = count;
        this.count = 0;
    }
}
""",
    "U04_DeadStoreInLoop": """public class U04_DeadStoreInLoop {
    public int sum(int[] values) {
        int total = 0;
        for (int i = 0; i < values.length; i++) {
            total = 0;
            total += values[i];
        }
        return total;
    }
}
""",
    "U05_LeakThenReturn": """import java.io.FileInputStream;

public class U05_LeakThenReturn {
    public int firstByte(String path) {
        FileInputStream fis = new FileInputStream(path);
        return fis.read();
    }
}
""",
    "U06_UnusedLocalInBranch": """public class U06_UnusedLocalInBranch {
    public int pick(boolean flag, int x) {
        if (flag) {
            int unused = x * 100;
            return x;
        }
        return x - 1;
    }
}
""",
    # --- harder variants of known classes ---
    "U07_DeadStoreThenUse": """public class U07_DeadStoreThenUse {
    public int compute(int input) {
        int result = 0;
        if (input > 0) {
            result = input * 2;
        } else {
            result = input * 3;
        }
        return result;
    }
}
""",
    "U08_LoopVariableWrite": """public class U08_LoopVariableWrite {
    public int find(int[] values, int target) {
        int found = -1;
        for (int i = 0; i < values.length; i++) {
            if (values[i] == target) {
                found = i;
            }
        }
        return found;
    }
}
""",
    "U09_MultiLeaksSameVar": """import java.io.FileInputStream;
import java.io.IOException;

public class U09_MultiLeaksSameVar {
    public int readTwo(String a, String b) {
        FileInputStream first = new FileInputStream(a);
        try {
            first.read();
        } catch (IOException e) {
            first.close();
        }
        FileInputStream second = new FileInputStream(b);
        try {
            return second.read();
        } catch (IOException e) {
            return 0;
        }
    }
}
""",
    "U10_DeadStoreInElse": """public class U10_DeadStoreInElse {
    public String label(int code) {
        String text = "unknown";
        if (code == 1) {
            text = "one";
        } else if (code == 2) {
            text = "two";
        }
        return text;
    }
}
""",
    "U11_UnusedLocalChain": """public class U11_UnusedLocalChain {
    public int chain(int input) {
        int a = input + 1;
        int b = a + 1;
        int c = b + 1;
        return input;
    }
}
""",
    "U12_FieldShadow": """public class U12_FieldShadow {
    private int total;

    public int add(int value) {
        int doubled = value * 2;
        total = total + doubled;
        return total;
    }
}
""",
}

print("=" * 64)
print("JAVA BENCH 3: 12 tasks — find Kappa's remaining gaps")
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
print("\n[step 1] staging 12 tasks, Sorald baseline")
for name, content in FILES.items():
    path = BENCH / f"{name}.java"
    path.write_text(content)
    before, per_rule = mine(path)
    pairs.append({"id": name, "file_path": str(path), "before": before})
    print(f"  {name:26s} {before} {per_rule if per_rule else ''}")

print("\n[step 2] Kappa (tree-sitter)")
kappa_dir = SP / "java3-kappa"
kappa_dir.mkdir(exist_ok=True)
for old in kappa_dir.glob("*.java"):
    old.unlink()
for pair in pairs:
    out_file = kappa_dir / f"{pair['id']}.java"
    r = subprocess.run(
        ["conda", "run", "-n", "benchmark", "python", "-m", "kappa",
         pair["file_path"], "--parser-backend", "tree-sitter",
         "--out", str(out_file), "--max-iters", "3"],
        capture_output=True, text=True, timeout=300, cwd=str(SRC),
    )
    k_after = mine(out_file)[0] if out_file.exists() else pair["before"]
    pair["kappa_after"] = k_after
    print(f"  {pair['id']:26s} {pair['before']} -> {k_after}")

print("\n[step 3] Gemma (RapidRouter)")
from llm.router import generate
gemma_dir = SP / "java3-gemma"
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
            print(f"  {pair['id']:26s} {pair['before']} -> {pair['gemma_after']}  ({time.perf_counter() - started:.1f}s)")
        else:
            pair["gemma_after"] = pair["before"]
            print(f"  {pair['id']:26s} EMPTY extraction")
    except Exception as exc:
        pair["gemma_after"] = pair["before"]
        print(f"  {pair['id']:26s} FAILED: {exc}")

print("\n" + "=" * 64)
print("SUMMARY (Sorald oracle)")
print("=" * 64)
print(f"  {'task':26s} {'before':>6s} {'kappa':>6s} {'gemma':>6s} {'union':>6s}")
rows = []
for pair in pairs:
    union_after = min(pair["kappa_after"], pair["gemma_after"])
    rows.append({"id": pair["id"], "before": pair["before"],
                 "kappa_after": pair["kappa_after"], "gemma_after": pair["gemma_after"],
                 "union_after": union_after})
    print(f"  {pair['id']:26s} {pair['before']:6d} {pair['kappa_after']:6d} {pair['gemma_after']:6d} {union_after:6d}")

kb = sum(r["before"] for r in rows)
ka = sum(r["kappa_after"] for r in rows)
ga = sum(r["gemma_after"] for r in rows)
ua = sum(r["union_after"] for r in rows)
print(f"\n  total violations : {kb}")
print(f"  kappa repaired   : {kb - ka} ({100 * (kb - ka) / max(kb, 1):.0f}%)  remaining={ka}")
print(f"  gemma repaired   : {kb - ga} ({100 * (kb - ga) / max(kb, 1):.0f}%)  remaining={ga}")
print(f"  UNION repaired   : {kb - ua} ({100 * (kb - ua) / max(kb, 1):.0f}%)  remaining={ua}")
(SP / "java3-summary.json").write_text(json.dumps(rows, indent=2) + "\n")
print(f"\ndetails -> {SP / 'java3-summary.json'}")
