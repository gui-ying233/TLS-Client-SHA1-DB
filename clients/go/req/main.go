package main

import (
	"encoding/json"
	"fmt"
	"github.com/imroc/req/v3"
	"os"
	"runtime"
	"strings"
	"time"
)

const clientVersion = "3.53.0"
const depName = "req"

func output(body []byte) {
	var echo interface{}
	if err := json.Unmarshal(body, &echo); err != nil {
		panic(err)
	}
	v := strings.TrimPrefix(runtime.Version(), "go")
	value := map[string]interface{}{
		"runtime_version": v, "client_version": clientVersion,
		"environment": map[string]interface{}{
			"os": runtime.GOOS, "arch": runtime.GOARCH,
			"tls_backend":  map[string]string{"name": "crypto/tls", "version": runtime.Version()},
			"dependencies": map[string]string{depName: clientVersion},
		}, "echo": echo,
	}
	if err := json.NewEncoder(os.Stdout).Encode(value); err != nil {
		panic(err)
	}
}
func endpoint() string {
	if len(os.Args) != 3 || os.Args[1] != clientVersion {
		panic("expected client version and endpoint")
	}
	if !strings.HasPrefix(os.Args[2], "https://") {
		panic("HTTPS required")
	}
	return os.Args[2]
}

func main() {
	uri := endpoint()
	// Intentionally use Req's ordinary TLS stack. Never enable SetTLSFingerprint* or browser presets.
	client := req.C().SetTimeout(25 * time.Second).SetRedirectPolicy(req.NoRedirectPolicy())
	response, err := client.R().SetHeader("Accept", "application/json").SetHeader("Connection", "close").Get(uri)
	if err != nil {
		panic(err)
	}
	if response.GetStatusCode() < 200 || response.GetStatusCode() >= 300 {
		panic(fmt.Sprintf("HTTP %d", response.GetStatusCode()))
	}
	output(response.Bytes())
}
