package main

import (
	"encoding/json"
	"fmt"
	"github.com/valyala/fasthttp"
	"os"
	"runtime"
	"strings"
	"time"
)

const clientVersion = "1.65.0"
const depName = "fasthttp"

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
	client := &fasthttp.Client{ReadTimeout: 25 * time.Second, WriteTimeout: 25 * time.Second, MaxConnsPerHost: 1, MaxIdemponentCallAttempts: 1}
	request := fasthttp.AcquireRequest()
	defer fasthttp.ReleaseRequest(request)
	response := fasthttp.AcquireResponse()
	defer fasthttp.ReleaseResponse(response)
	request.SetRequestURI(uri)
	request.Header.SetMethod("GET")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Connection", "close")
	if err := client.DoTimeout(request, response, 25*time.Second); err != nil {
		panic(err)
	}
	if response.StatusCode() < 200 || response.StatusCode() >= 300 {
		panic(fmt.Sprintf("HTTP %d", response.StatusCode()))
	}
	output(response.Body())
}
