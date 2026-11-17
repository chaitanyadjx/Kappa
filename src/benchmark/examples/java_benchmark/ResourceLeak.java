import java.io.FileInputStream;
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
