package main

import (
	"crypto/tls"
	"encoding/json"
	"fmt"
	"github.com/valyala/fasthttp"
	"os"
	"runtime"
	"runtime/debug"
	"strings"
	"time"
)

var clientVersion = dependencyVersion("github.com/valyala/fasthttp")
var verify = os.Getenv("FPDB_TLS_VERIFY") != "false"

const depName = "fasthttp"

func output(body []byte) {
	var echo interface{}
	if err := json.Unmarshal(body, &echo); err != nil {
		panic(err)
	}
	v := strings.TrimPrefix(runtime.Version(), "go")
	value := map[string]interface{}{
		"runtime_version": v, "client_version": clientVersion, "verify": verify,
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
	if !verify {
		client.TLSConfig = &tls.Config{InsecureSkipVerify: true}
	}
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

func dependencyVersion(module string) string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		panic("missing Go build information")
	}
	for _, dep := range info.Deps {
		if dep.Path == module {
			if dep.Replace != nil {
				panic("unexpected replaced client module")
			}
			return strings.TrimPrefix(dep.Version, "v")
		}
	}
	panic("client dependency missing from Go build information: " + module)
}
