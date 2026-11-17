public class HardcodedSecret {
    private static final String DB_PASSWORD = "super_secret_123";
    private String apiToken = "abc123def456";

    public boolean authenticate(String user, String pass) {
        return DB_PASSWORD.equals(pass) && apiToken.length() > 0;
    }
}
