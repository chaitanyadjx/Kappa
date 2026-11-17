import java.io.FileInputStream;
import java.io.IOException;

public class KappaViolations {

    private String apiKey = "sk-1234567890abcdef";

    public int process(int[] data) {
        int total = 0;
        for (int i = 0; i < data.length; i++) {
            total = total + sumValue(data[i]);
        }
        FileInputStream fis = new FileInputStream("config.txt");
        try {
            return total + fis.read();
        } catch (IOException e) {
            return 0;
        }
    }

    public int sumValue(int value) {
        return value * 2;
    }

    public String getConfig() {
        if (apiKey.length() > 0) {
            return apiKey;
        }
        return "default";
        // unreachable
        System.out.println("never reached");
    }

    public void unusedMethod() {
        int deadCode = 42;
    }
}
