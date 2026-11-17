# Kappa Spoon adapter (Java deep analysis)

A tiny Maven project that runs [Spoon](https://spoon.gforge.inria.fr/) (`fr.inria.gforge.spoon:spoon-core`)
over one Java file and prints the same 6-category Kappa Knowledge Base JSON to stdout that
`Parser.py` produces for Python.

## Files

- `pom.xml` — builds a shaded (fat) jar `target/kappa-spoon-extractor.jar` with
  `Main-Class: kappaspoon.SpoonExtractor`; also registers `exec-maven-plugin`.
- `src/main/java/kappaspoon/SpoonExtractor.java` — the extractor.

## Build + run

```bash
mvn -q -f pom.xml -DskipTests package          # build once, cache the jar
java -jar target/kappa-spoon-extractor.jar <file.java> [display-name]
# or: mvn -q exec:java -Dexec.args="<file.java>"
```

## How Parser.py uses it

`parse_source(..., backend="spoon")` (Java only) in `Parser.py`:

1. reuses `spoon/target/kappa-spoon-extractor.jar` if present;
2. otherwise attempts `mvn -q -f <spoon dir>/pom.xml -DskipTests package` **once per process**;
3. runs `java -jar <jar> <tmpfile.java> <name>` and parses the JSON from stdout;
4. if `mvn`/`java`/the jar is unavailable, or the subprocess fails, prints a one-line warning to
   stderr and falls back to the `tree-sitter`-java backend.

This project is optional: the pipeline never requires it, and the tree-sitter fallback keeps the
KB schema identical (same keys, same 6 categories, deterministic ids).
