package main

import (
	"crypto/tls"
	"encoding/json"
	"fmt"
	"github.com/gocolly/colly/v2"
	"net/http"
	"os"
	"runtime"
	"runtime/debug"
	"strings"
	"time"
)

var clientVersion = dependencyVersion("github.com/gocolly/colly/v2")
var verify = os.Getenv("FPDB_TLS_VERIFY") != "false"

const depName = "colly"

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
	collector := colly.NewCollector(colly.MaxBodySize(1<<20), colly.IgnoreRobotsTxt())
	collector.WithTransport(transport)
	collector.SetRedirectHandler(func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse })
	requestCount := 0
	collector.OnRequest(func(r *colly.Request) {
		requestCount++
		if requestCount > 1 {
			r.Abort()
			panic("probe attempted more than one request")
		}
		r.Headers.Set("Accept", "application/json")
		r.Headers.Set("Connection", "close")
	})
	var body []byte
	var status int
	collector.OnResponse(func(r *colly.Response) {
		status = r.StatusCode
		body = r.Body
	})
	var requestError error
	collector.OnError(func(r *colly.Response, err error) { requestError = err })
	if err := collector.Visit(uri); err != nil {
		panic(err)
	}
	if requestError != nil {
		panic(requestError)
	}
	if status < 200 || status >= 300 {
		panic(fmt.Sprintf("HTTP %d", status))
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
