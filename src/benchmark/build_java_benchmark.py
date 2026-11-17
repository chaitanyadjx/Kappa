"""Build a Java benchmark corpus for Kappa: multiple files with SonarJava
violations matching Kappa's rule classes (undefined names, unreachable code,
resource leaks, hardcoded secrets, unused code). Verified by Sorald mine.
"""

import subprocess
from pathlib import Path

SRC = Path("/home/chaitanya/Projects/final-year-project/src")
JAR = SRC / "benchmark" / "sorald-0.8.6-jar-with-dependencies.jar"
OUT = SRC / "benchmark" / "examples" / "java_benchmark"
OUT.mkdir(exist_ok=True)

FILES = {
    "ResourceLeak.java": """import java.io.FileInputStream;
import java.io.IOException;

public class ResourceLeak {
    public int readConfig(String path) {
        FileInputStream fis = new FileInputStream(path);
        try {
            return fis.read();
        } catch (IOException e) {
            return 0;
        }
    }
}
""",
    "UnreachableCode.java": """public class UnreachableCode {
    public String getStatus(boolean active) {
        if (active) {
            return "active";
        }
        return "inactive";
        System.out.println("never reached");
    }

    public int getValue() {
        return 42;
        int unused = 10;
    }
}
""",
    "UnusedAssignment.java": """public class UnusedAssignment {
    public int compute(int input) {
        int result = getValue();
        result = input * 2;
        return result;
    }

    private int getValue() {
        return 10;
    }
}
""",
    "HardcodedSecret.java": """public class HardcodedSecret {
    private static final String DB_PASSWORD = "super_secret_123";
    private String apiToken = "abc123def456";

    public boolean authenticate(String user, String pass) {
        return DB_PASSWORD.equals(pass) && apiToken.length() > 0;
    }
}
""",
    "UndefinedCall.java": """public class UndefinedCall {
    public double calculate(int[] values) {
        double avg = computeAverage(values);
        return avg * getScale();
    }
}
""",
    "MissingImport.java": """public class MissingImport {
    public String process() {
        List<String> items = new ArrayList<>();
        items.add("test");
        return String.join(",", items);
    }
}
""",
}

for name, content in FILES.items():
    (OUT / name).write_text(content)
    print(f"  wrote {name}")

print(f"\nverifying with Sorald mine...")
for name in FILES:
    r = subprocess.run(
        ["java", "-jar", str(JAR), "mine", "--source", str(OUT / name),
         "--rule-keys", "S1854,S2095,S2068,S1488,S1149"],
        capture_output=True, text=True, timeout=120,
    )
    violations = []
    for line in r.stdout.splitlines():
        if "=" in line and not line.startswith("SLF4J"):
            key, val = line.strip().split("=", 1)
            if int(val) > 0:
                violations.append(f"{key}={val}")
    print(f"  {name}: {', '.join(violations) if violations else 'CLEAN'}")

print(f"\nbenchmark corpus at {OUT}")
