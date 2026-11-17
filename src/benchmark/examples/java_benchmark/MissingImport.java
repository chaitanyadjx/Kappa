public class MissingImport {
    public String process() {
        List<String> items = new ArrayList<>();
        items.add("test");
        return String.join(",", items);
    }
}
