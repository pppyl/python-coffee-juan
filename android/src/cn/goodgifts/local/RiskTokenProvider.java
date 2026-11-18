package cn.goodgifts.local;

interface RiskTokenProvider {
    String get(long timeoutMs) throws Exception;
    void close();
}
