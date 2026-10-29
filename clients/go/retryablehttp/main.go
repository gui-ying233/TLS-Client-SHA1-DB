package main

import (
	"crypto/tls"
	"encoding/json"
	"fmt"
	"github.com/hashicorp/go-retryablehttp"
	"io"
	"net/http"
	"os"
	"runtime"
	"runtime/debug"
	"strings"
	"time"
)

var clientVersion = dependencyVersion("github.com/hashicorp/go-retryablehttp")
var verify = os.Getenv("FPDB_TLS_VERIFY") != "false"

const depName = "go-retryablehttp"

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
	transport := &http.Transport{Proxy: nil, DisableKeepAlives: true, TLSHandshakeTimeout: 20 * time.Second}
	if !verify {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
	}
	defer transport.CloseIdleConnections()
	client := retryablehttp.NewClient()
	client.RetryMax = 0 // One independent TLS handshake per probe; collector owns retries.
	client.Logger = nil
	client.HTTPClient.Transport = transport
	client.HTTPClient.Timeout = 25 * time.Second
	client.HTTPClient.CheckRedirect = func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }
	request, err := retryablehttp.NewRequest("GET", uri, nil)
	if err != nil {
		panic(err)
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Connection", "close")
	response, err := client.Do(request)
	if err != nil {
		panic(err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		panic(fmt.Sprintf("HTTP %d", response.StatusCode))
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		panic(err)
	}
	output(body)
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
