package mazzitelli.model.payload;

import io.quarkus.runtime.annotations.RegisterForReflection;

@RegisterForReflection
public class Capability {
    public String key;
    public String value;
    
    public Capability() {}

    public Capability(String key, String value) {
        this.key = key;
        this.value = value;
    }

    public String getKey() {
        return key;
    }

    public void setKey(String key) {
        this.key = key;
    }

    public String getValue() {
        return value;
    }

    public void setValue(String value) {
        this.value = value;
    }
}
