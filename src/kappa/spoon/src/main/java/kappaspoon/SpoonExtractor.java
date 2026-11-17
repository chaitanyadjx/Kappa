package kappaspoon;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import spoon.Launcher;
import spoon.reflect.CtModel;
import spoon.reflect.code.CtAssignment;
import spoon.reflect.code.CtCatch;
import spoon.reflect.code.CtConstructorCall;
import spoon.reflect.code.CtDo;
import spoon.reflect.code.CtExpression;
import spoon.reflect.code.CtForEach;
import spoon.reflect.code.CtFor;
import spoon.reflect.code.CtIf;
import spoon.reflect.code.CtInvocation;
import spoon.reflect.code.CtLiteral;
import spoon.reflect.code.CtLocalVariable;
import spoon.reflect.code.CtOperatorAssignment;
import spoon.reflect.code.CtReturn;
import spoon.reflect.code.CtSwitch;
import spoon.reflect.code.CtSwitchExpression;
import spoon.reflect.code.CtThrow;
import spoon.reflect.code.CtTry;
import spoon.reflect.code.CtTryWithResource;
import spoon.reflect.code.CtVariableAccess;
import spoon.reflect.code.CtVariableWrite;
import spoon.reflect.code.CtWhile;
import spoon.reflect.cu.SourcePosition;
import spoon.reflect.declaration.CtClass;
import spoon.reflect.declaration.CtConstructor;
import spoon.reflect.declaration.CtElement;
import spoon.reflect.declaration.CtEnum;
import spoon.reflect.declaration.CtExecutable;
import spoon.reflect.declaration.CtField;
import spoon.reflect.declaration.CtImport;
import spoon.reflect.declaration.CtInterface;
import spoon.reflect.declaration.CtMethod;
import spoon.reflect.declaration.CtParameter;
import spoon.reflect.declaration.CtVariable;
import spoon.reflect.visitor.filter.TypeFilter;

/**
 * Prints the Kappa 6-category Knowledge Base JSON for one Java file to stdout.
 * Usage: java -jar kappa-spoon-extractor.jar <file.java> [display-name]
 */
public final class SpoonExtractor {

    private static final Map<String, String> RESOURCE_KINDS = buildResourceKinds();
    private static final Pattern SECRET_NAME = Pattern.compile("(?i)(password|secret|token|api_key)\\b");
    private static final List<String> CATEGORIES = List.of(
            "calls", "library_usage", "dataflow", "configuration", "control_flow", "security");

    private final Map<String, List<Map<String, Object>>> categories = new HashMap<>();
    private final List<Object[]> pendingResources = new ArrayList<>();
    private int seq = 0;

    private SpoonExtractor() {
        for (String category : CATEGORIES) {
            categories.put(category, new ArrayList<>());
        }
    }

    private static Map<String, String> buildResourceKinds() {
        Map<String, String> kinds = new HashMap<>();
        for (String n : new String[]{"file", "fileinputstream", "fileoutputstream", "filereader",
                "filewriter", "bufferedreader", "bufferedwriter", "printwriter", "printstream",
                "scanner", "inputstreamreader", "randomaccessfile"}) {
            kinds.put(n, "file");
        }
        for (String n : new String[]{"connection", "statement", "preparedstatement", "resultset"}) {
            kinds.put(n, "db");
        }
        for (String n : new String[]{"socket", "serversocket", "httpurlconnection"}) {
            kinds.put(n, "socket");
        }
        return kinds;
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("usage: java -jar kappa-spoon-extractor.jar <file.java> [display-name]");
            System.exit(2);
        }
        Path file = Paths.get(args[0]);
        String displayName = args.length > 1 ? args[1] : file.getFileName().toString();
        byte[] bytes = Files.readAllBytes(file);
        Launcher launcher = new Launcher();
        launcher.getEnvironment().setNoClasspath(true);
        launcher.getEnvironment().setCommentEnabled(false);
        launcher.addInputResource(file.toAbsolutePath().toString());
        CtModel model;
        try {
            model = launcher.buildModel();
        } catch (Exception e) {
            System.err.println("spoon parse failed: " + e.getMessage());
            System.exit(3);
            return;
        }
        String json = new SpoonExtractor().extract(model, displayName, bytes);
        System.out.println(json);
    }

    private String extract(CtModel model, String displayName, byte[] bytes) {
        extractSymbols(model);
        extractImports(model);
        extractCalls(model);
        extractVariables(model);
        extractAssignments(model);
        extractParameters(model);
        extractControlFlow(model);
        resolvePending(model);
        return renderKb(displayName, bytes);
    }

    private void extractSymbols(CtModel model) {
        for (CtClass<?> t : model.getElements(new TypeFilter<>(CtClass.class))) {
            if (!t.isAnonymous()) {
                addSymbol(t.getSimpleName(), "class", lineOf(t));
            }
        }
        for (CtInterface<?> t : model.getElements(new TypeFilter<>(CtInterface.class))) {
            addSymbol(t.getSimpleName(), "class", lineOf(t));
        }
        for (CtEnum<?> t : model.getElements(new TypeFilter<>(CtEnum.class))) {
            addSymbol(t.getSimpleName(), "class", lineOf(t));
        }
        for (CtMethod<?> m : model.getElements(new TypeFilter<>(CtMethod.class))) {
            addSymbol(m.getSimpleName(), "function", lineOf(m));
        }
        for (CtConstructor<?> c : model.getElements(new TypeFilter<>(CtConstructor.class))) {
            addSymbol(c.getSimpleName(), "function", lineOf(c));
        }
    }

    private void extractImports(CtModel model) {
        for (CtImport imp : model.getElements(new TypeFilter<>(CtImport.class))) {
            Map<String, Object> e = entry("library_usage", lineOf(imp), colOf(imp));
            e.put("module", imp.getReference() == null ? "<unknown>" : imp.getReference().toString());
            e.put("names", new ArrayList<String>());
            e.put("alias", null);
        }
    }

    private void extractCalls(CtModel model) {
        for (CtInvocation<?> inv : model.getElements(new TypeFilter<>(CtInvocation.class))) {
            int line = lineOf(inv);
            int col = colOf(inv);
            String name = inv.getExecutable() != null ? inv.getExecutable().getSimpleName() : "<unknown>";
            String receiver = inv.getTarget() != null ? inv.getTarget().toString() : null;
            List<String> args = new ArrayList<>();
            if (inv.getArguments() != null) {
                for (CtExpression<?> a : inv.getArguments()) {
                    args.add(a == null ? "" : a.toString());
                }
            }
            Map<String, Object> e = entry("calls", line, col);
            e.put("col", col);
            e.put("name", name);
            e.put("receiver", receiver);
            e.put("args", args);
            e.put("in_function", inFunction(inv));
            String receiverLower = receiver == null ? "" : receiver.toLowerCase(Locale.ROOT);
            if ("exec".equals(name) && receiverLower.contains("runtime")) {
                Map<String, Object> s = entry("security", line, col);
                s.put("rule", "runtime_exec");
                s.put("detail", "Runtime.exec() call");
            }
            if (("command".equals(name) || "start".equals(name)) && receiverLower.contains("processbuilder")) {
                Map<String, Object> s = entry("security", line, col);
                s.put("rule", "process_builder");
                s.put("detail", "ProcessBuilder command");
            }
            String targetBase = receiver == null ? "" : receiver.substring(receiver.lastIndexOf('.') + 1);
            if ("getenv".equals(name) && "system".equalsIgnoreCase(targetBase)) {
                Map<String, Object> c = entry("configuration", line, col);
                c.put("kind", "env");
                c.put("name", firstArgString(inv));
                c.put("has_default", inv.getArguments() != null && inv.getArguments().size() >= 2);
            } else if ("getProperty".equals(name) && "system".equalsIgnoreCase(targetBase)) {
                Map<String, Object> c = entry("configuration", line, col);
                c.put("kind", "property");
                c.put("name", firstArgString(inv));
                c.put("has_default", inv.getArguments() != null && inv.getArguments().size() >= 2);
            }
            if ("close".equals(name) && receiver != null && !receiver.contains("(")) {
                String base = receiver.substring(receiver.lastIndexOf('.') + 1).trim();
                markClosed(base);
            }
        }
        for (CtConstructorCall<?> cc : model.getElements(new TypeFilter<>(CtConstructorCall.class))) {
            int line = lineOf(cc);
            int col = colOf(cc);
            String typeName = cc.getType() != null ? cc.getType().getSimpleName() : "<new>";
            List<String> args = new ArrayList<>();
            if (cc.getArguments() != null) {
                for (CtExpression<?> a : cc.getArguments()) {
                    args.add(a == null ? "" : a.toString());
                }
            }
            Map<String, Object> e = entry("calls", line, col);
            e.put("col", col);
            e.put("name", typeName);
            e.put("receiver", null);
            e.put("args", args);
            e.put("in_function", inFunction(cc));
            if ("processbuilder".equalsIgnoreCase(typeName)) {
                Map<String, Object> s = entry("security", line, col);
                s.put("rule", "process_builder");
                s.put("detail", "new ProcessBuilder(...)");
            }
        }
    }

    private void extractVariables(CtModel model) {
        for (CtLocalVariable<?> lv : model.getElements(new TypeFilter<>(CtLocalVariable.class))) {
            if (lv.getParent() instanceof CtTryWithResource) {
                continue;
            }
            recordVariable(lv, lv.getSimpleName(), lv.getDefaultExpression(),
                    lv.getParent() instanceof CtForEach ? "for" : "assign");
        }
        for (CtField<?> f : model.getElements(new TypeFilter<>(CtField.class))) {
            recordVariable(f, f.getSimpleName(), f.getDefaultExpression(), "assign");
        }
    }

    private void recordVariable(CtVariable<?> var, String name, CtExpression<?> init, String assignKind) {
        int line = lineOf(var);
        int col = colOf(var);
        String func = inFunction(var);
        if (init instanceof CtLiteral && SECRET_NAME.matcher(name).find()) {
            Map<String, Object> s = entry("security", lineOf(init), colOf(init));
            s.put("rule", "hardcoded_secret");
            s.put("detail", "literal assigned to '" + name + "'");
        }
        String op = resourceOp(init);
        if (op != null) {
            Map<String, Object> e = entry("dataflow", lineOf(init), colOf(init));
            e.put("kind", RESOURCE_KINDS.get(op.toLowerCase(Locale.ROOT)));
            e.put("op", op);
            e.put("name", name);
            e.put("closed", Boolean.FALSE);
            pendingResources.add(new Object[]{name, e});
            return;
        }
        Map<String, Object> e = entry("dataflow", line, col);
        e.put("kind", assignKind);
        e.put("name", name);
        e.put("in_function", func);
    }

    private void extractAssignments(CtModel model) {
        for (CtAssignment<?, ?> a : model.getElements(new TypeFilter<>(CtAssignment.class))) {
            int line = lineOf(a);
            int col = colOf(a);
            String func = inFunction(a);
            CtExpression<?> assigned = a.getAssigned();
            CtExpression<?> value = a.getAssignment();
            String name;
            if (assigned instanceof CtVariableWrite && ((CtVariableWrite<?>) assigned).getVariable() != null) {
                name = ((CtVariableWrite<?>) assigned).getVariable().getSimpleName();
            } else {
                name = assigned == null ? "<unknown>" : assigned.toString();
            }
            if (value instanceof CtLiteral && SECRET_NAME.matcher(name).find()) {
                Map<String, Object> s = entry("security", lineOf(value), colOf(value));
                s.put("rule", "hardcoded_secret");
                s.put("detail", "literal assigned to '" + name + "'");
            }
            String op = resourceOp(value);
            if (op != null) {
                Map<String, Object> e = entry("dataflow", lineOf(value), colOf(value));
                e.put("kind", RESOURCE_KINDS.get(op.toLowerCase(Locale.ROOT)));
                e.put("op", op);
                e.put("name", name);
                e.put("closed", Boolean.FALSE);
                pendingResources.add(new Object[]{name, e});
            } else {
                Map<String, Object> e = entry("dataflow", line, col);
                e.put("kind", a instanceof CtOperatorAssignment ? "aug" : "assign");
                e.put("name", name);
                e.put("in_function", func);
            }
        }
    }

    private void extractParameters(CtModel model) {
        for (CtParameter<?> p : model.getElements(new TypeFilter<>(CtParameter.class))) {
            Map<String, Object> e = entry("dataflow", lineOf(p), colOf(p));
            e.put("kind", "param");
            e.put("name", p.getSimpleName());
            e.put("in_function", inFunction(p));
        }
    }

    private void extractControlFlow(CtModel model) {
        for (CtIf i : model.getElements(new TypeFilter<>(CtIf.class))) {
            Map<String, Object> e = entry("control_flow", lineOf(i), colOf(i));
            e.put("kind", "if");
            e.put("in_function", inFunction(i));
            CtElement elseStatement = i.getElseStatement();
            if (elseStatement != null) {
                Map<String, Object> el = entry("control_flow", lineOf(elseStatement), colOf(elseStatement));
                el.put("kind", elseStatement instanceof CtIf ? "elif" : "else");
                el.put("in_function", inFunction(elseStatement));
            }
        }
        for (CtFor f : model.getElements(new TypeFilter<>(CtFor.class))) {
            flow(f, "for");
        }
        for (CtForEach f : model.getElements(new TypeFilter<>(CtForEach.class))) {
            flow(f, "for");
        }
        for (CtWhile w : model.getElements(new TypeFilter<>(CtWhile.class))) {
            flow(w, "while");
        }
        for (CtDo d : model.getElements(new TypeFilter<>(CtDo.class))) {
            flow(d, "do");
        }
        for (CtSwitch<?> s : model.getElements(new TypeFilter<>(CtSwitch.class))) {
            flow(s, "switch");
        }
        for (CtSwitchExpression<?> s : model.getElements(new TypeFilter<>(CtSwitchExpression.class))) {
            flow(s, "switch");
        }
        for (CtTry t : model.getElements(new TypeFilter<>(CtTry.class))) {
            flow(t, "try");
            CtElement finalizer = t.getFinalizer();
            if (finalizer != null) {
                Map<String, Object> f = entry("control_flow", lineOf(finalizer), colOf(finalizer));
                f.put("kind", "finally");
                f.put("in_function", inFunction(finalizer));
            }
        }
        for (CtCatch c : model.getElements(new TypeFilter<>(CtCatch.class))) {
            flow(c, "catch");
        }
        for (CtReturn<?> r : model.getElements(new TypeFilter<>(CtReturn.class))) {
            flow(r, "return");
        }
        for (CtThrow t : model.getElements(new TypeFilter<>(CtThrow.class))) {
            flow(t, "throw");
        }
        for (CtTryWithResource tr : model.getElements(new TypeFilter<>(CtTryWithResource.class))) {
            if (tr.getResources() == null) {
                continue;
            }
            for (CtExpression<?> res : tr.getResources()) {
                if (res instanceof CtVariableAccess) {
                    String name = ((CtVariableAccess<?>) res).getVariable().getSimpleName();
                    markClosed(name);
                } else if (res instanceof CtLocalVariable) {
                    CtLocalVariable<?> lv = (CtLocalVariable<?>) res;
                    String op = resourceOp(lv.getDefaultExpression());
                    Map<String, Object> e = entry("dataflow", lineOf(lv.getDefaultExpression() != null
                            ? lv.getDefaultExpression() : lv), colOf(lv.getDefaultExpression() != null
                            ? lv.getDefaultExpression() : lv));
                    e.put("kind", op != null ? RESOURCE_KINDS.get(op.toLowerCase(Locale.ROOT)) : "resource");
                    e.put("op", op != null ? op : "resource");
                    e.put("name", lv.getSimpleName());
                    e.put("closed", Boolean.TRUE);
                }
            }
        }
    }

    private void resolvePending(CtModel model) {
        for (Object[] pending : pendingResources) {
            String name = (String) pending[0];
            @SuppressWarnings("unchecked")
            Map<String, Object> entry = (Map<String, Object>) pending[1];
            if (Boolean.TRUE.equals(entry.get("closed"))) {
                continue;
            }
            for (CtInvocation<?> inv : model.getElements(new TypeFilter<>(CtInvocation.class))) {
                if (inv.getExecutable() != null && "close".equals(inv.getExecutable().getSimpleName())
                        && inv.getTarget() != null && !inv.getTarget().toString().contains("(")
                        && inv.getTarget().toString().substring(
                                inv.getTarget().toString().lastIndexOf('.') + 1).trim().equals(name)) {
                    entry.put("closed", Boolean.TRUE);
                    break;
                }
            }
        }
        pendingResources.clear();
    }

    private void flow(CtElement element, String kind) {
        Map<String, Object> e = entry("control_flow", lineOf(element), colOf(element));
        e.put("kind", kind);
        e.put("in_function", inFunction(element));
    }

    private String resourceOp(CtExpression<?> init) {
        if (init instanceof CtConstructorCall && ((CtConstructorCall<?>) init).getType() != null) {
            String typeName = ((CtConstructorCall<?>) init).getType().getSimpleName();
            if (RESOURCE_KINDS.containsKey(typeName.toLowerCase(Locale.ROOT))) {
                return typeName;
            }
        }
        return null;
    }

    private String firstArgString(CtInvocation<?> inv) {
        if (inv.getArguments() == null || inv.getArguments().isEmpty()) {
            return "<unknown>";
        }
        CtExpression<?> first = inv.getArguments().get(0);
        if (first instanceof CtLiteral && first.getValue() instanceof String) {
            return (String) first.getValue();
        }
        return first.toString();
    }

    private void markClosed(String name) {
        for (Object[] pending : pendingResources) {
            if (name.equals(pending[0])) {
                @SuppressWarnings("unchecked")
                Map<String, Object> entry = (Map<String, Object>) pending[1];
                entry.put("closed", Boolean.TRUE);
            }
        }
    }

    private void addSymbol(String name, String kind, int line) {
        Map<String, Object> symbol = new LinkedHashMap<>();
        symbol.put("name", name);
        symbol.put("kind", kind);
        symbol.put("line", line);
        for (Map<String, Object> existing : symbols) {
            if (existing.get("name").equals(name)) {
                return;
            }
        }
        symbols.add(symbol);
    }

    private final List<Map<String, Object>> symbols = new ArrayList<>();

    private Map<String, Object> entry(String category, int line, int col) {
        Map<String, Object> e = new LinkedHashMap<>();
        e.put("_seq", seq++);
        e.put("_line", line);
        e.put("_col", col);
        categories.get(category).add(e);
        return e;
    }

    private String renderKb(String displayName, byte[] bytes) {
        Map<String, Object> kb = new LinkedHashMap<>();
        kb.put("file", displayName);
        kb.put("source_hash", sha256(bytes));
        symbols.sort(Comparator.comparingInt((Map<String, Object> s) -> (Integer) s.get("line"))
                .thenComparing(s -> String.valueOf(s.get("name"))));
        Map<String, Object> symbolsMap = new LinkedHashMap<>();
        symbolsMap.put("defined", symbols);
        kb.put("symbols", symbolsMap);
        Map<String, Object> cats = new LinkedHashMap<>();
        for (String category : CATEGORIES) {
            cats.put(category, finalizeCategory(category));
        }
        kb.put("categories", cats);
        String text = new String(bytes, StandardCharsets.UTF_8);
        int lines = 0;
        if (!text.isEmpty()) {
            lines = text.split("\r\n|\r|\n", -1).length;
            if (text.endsWith("\n") || text.endsWith("\r")) {
                lines--;
            }
        }
        kb.put("lines", lines);
        StringBuilder sb = new StringBuilder();
        writeJson(sb, kb);
        return sb.toString();
    }

    private List<Map<String, Object>> finalizeCategory(String category) {
        List<Map<String, Object>> entries = categories.get(category);
        entries.sort(Comparator
                .comparingInt((Map<String, Object> e) -> (Integer) e.get("_line"))
                .thenComparingInt(e -> (Integer) e.get("_col"))
                .thenComparingInt(e -> (Integer) e.get("_seq")));
        Set<String> used = new HashSet<>();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> e : entries) {
            int line = (Integer) e.get("_line");
            int col = (Integer) e.get("_col");
            boolean isCalls = "calls".equals(category);
            String base = category + ":L" + line + (isCalls ? ":C" + col : "");
            if (!used.add(base)) {
                String alt = isCalls ? base : base + ":C" + col;
                if (!isCalls && !used.contains(alt)) {
                    base = alt;
                } else {
                    int n = 2;
                    while (used.contains(base + ":" + n)) {
                        n++;
                    }
                    base = base + ":" + n;
                }
            }
            used.add(base);
            Map<String, Object> outEntry = new LinkedHashMap<>();
            outEntry.put("id", base);
            for (Map.Entry<String, Object> field : e.entrySet()) {
                if (!field.getKey().startsWith("_")) {
                    outEntry.put(field.getKey(), field.getValue());
                }
            }
            out.add(outEntry);
        }
        return out;
    }

    private int lineOf(CtElement element) {
        SourcePosition pos = element.getPosition();
        return pos != null && pos.isValidPosition() ? pos.getLine() : 0;
    }

    private int colOf(CtElement element) {
        SourcePosition pos = element.getPosition();
        return pos != null && pos.isValidPosition() ? pos.getColumn() : 0;
    }

    private String inFunction(CtElement element) {
        try {
            CtExecutable<?> exec = element.getParent(CtExecutable.class);
            if (exec == null) {
                return null;
            }
            if (exec instanceof CtMethod) {
                return exec.getSimpleName();
            }
            if (exec instanceof CtConstructor) {
                return exec.getSimpleName();
            }
            return "<lambda>";
        } catch (Exception e) {
            return null;
        }
    }

    private static String sha256(byte[] bytes) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            StringBuilder sb = new StringBuilder();
            for (byte b : digest.digest(bytes)) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private static void writeJson(StringBuilder sb, Object value) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof String) {
            writeJsonString(sb, (String) value);
        } else if (value instanceof Boolean || value instanceof Integer || value instanceof Long) {
            sb.append(value);
        } else if (value instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) value).entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeJsonString(sb, String.valueOf(e.getKey()));
                sb.append(':');
                writeJson(sb, e.getValue());
            }
            sb.append('}');
        } else if (value instanceof Collection) {
            sb.append('[');
            boolean first = true;
            for (Object item : (Collection<?>) value) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                writeJson(sb, item);
            }
            sb.append(']');
        } else {
            writeJsonString(sb, value.toString());
        }
    }

    private static void writeJsonString(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':
                    sb.append("\\\"");
                    break;
                case '\\':
                    sb.append("\\\\");
                    break;
                case '\n':
                    sb.append("\\n");
                    break;
                case '\r':
                    sb.append("\\r");
                    break;
                case '\t':
                    sb.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
    }
}
