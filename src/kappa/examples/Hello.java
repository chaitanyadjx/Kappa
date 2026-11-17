import java.io.FileInputStream;

public class Hello {
    private static final String DB_PASSWORD = "hunter2";
    private FileInputStream fin = new FileInputStream("f");

    public String readKey() {
        int n = helper(3);
        String key = System.getenv("KEY");
        if (key != null) {
            return key.trim() + n;
        }
        return "fallback";
    }

    public void run(String cmd) throws Exception {
        Runtime.getRuntime().exec(cmd);
    }
}
