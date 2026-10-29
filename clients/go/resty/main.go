package main

import (
	"crypto/tls"
	"encoding/json"
	"fmt"
	"github.com/go-resty/resty/v2"
	"net/http"
	"os"
	"runtime"
	"runtime/debug"
	"strings"
	"time"
)

func main() {
	verify := os.Getenv("FPDB_TLS_VERIFY") != "false"
	version := dependencyVersion("github.com/go-resty/resty/v2")
	if len(os.Args) != 3 || os.Args[1] != version {
		panic("usage: fpdb-resty 2.16.5 URL")
	}
	transport := &http.Transport{Proxy: nil, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: !verify}, DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := resty.New().SetTransport(transport).SetTimeout(25 * time.Second).SetRedirectPolicy(resty.NoRedirectPolicy())
	resp, err := client.R().SetHeader("Accept", "application/json").SetHeader("Connection", "close").Get(os.Args[2])
	if err != nil {
		panic(err)
	}
	if resp.StatusCode() < 200 || resp.StatusCode() >= 300 {
		panic(fmt.Sprintf("HTTP %d", resp.StatusCode()))
	}
	var echo interface{}
	if err := json.Unmarshal(resp.Body(), &echo); err != nil {
		panic(err)
	}
	data := map[string]interface{}{
		"runtime_version": strings.TrimPrefix(runtime.Version(), "go"),
		"client_version":  version,
		"verify":          verify,
		"environment": map[string]interface{}{"os": runtime.GOOS, "arch": runtime.GOARCH,
			"tls_backend":  map[string]string{"name": "crypto/tls", "version": runtime.Version()},
			"dependencies": map[string]string{"resty": version}},
		"echo": echo,
	}
	if err := json.NewEncoder(os.Stdout).Encode(data); err != nil {
		panic(err)
	}
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
