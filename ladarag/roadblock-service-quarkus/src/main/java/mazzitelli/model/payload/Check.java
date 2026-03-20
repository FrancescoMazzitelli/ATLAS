package mazzitelli.model.payload;

import com.fasterxml.jackson.annotation.JsonProperty;

import io.quarkus.runtime.annotations.RegisterForReflection;

@RegisterForReflection
public class Check {
    @JsonProperty("TlsSkipVerify")
    public boolean tlsSkipVerify;
    @JsonProperty("Method")
    public String method;
    @JsonProperty("Http")
    public String http;
    @JsonProperty("Interval")
    public String interval;
    @JsonProperty("Timeout")
    public String timeout;
    @JsonProperty("DeregisterCriticalServiceAfter")
    public String deregister;
    
    public Check() {}

    public Check(boolean tlsSkipVerify, String method, String http, String interval, String timeout, String deregister) {
        this.tlsSkipVerify = tlsSkipVerify;
        this.method = method;
        this.http = http;
        this.interval = interval;
        this.timeout = timeout;
        this.deregister = deregister;
    }

    public boolean getTlsSkipVerify() {
        return tlsSkipVerify;
    }

    public void setTlsSkipVerify(boolean tlsSkipVerify) {
        this.tlsSkipVerify = tlsSkipVerify;
    }

    public String getMethod() {
        return method;
    }

    public void setMethod(String method) {
        this.method = method;
    }

    public String getHttp() {
        return http;
    }

    public void setHttp(String http) {
        this.http = http;
    }

    public String getInterval() {
        return interval;
    }

    public void setInterval(String interval) {
        this.interval = interval;
    }

    public String getTimeout() {
        return timeout;
    }

    public void setTimeout(String timeout) {
        this.timeout = timeout;
    }

    public String getDeregister() {
        return deregister;
    }

    public void setDeregister(String deregister) {
        this.deregister = deregister;
    }
}