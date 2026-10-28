package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"runtime"
	"runtime/debug"
	"strings"
	"time"

	"github.com/parnurzeal/gorequest"
)

func main() {
	version := dependencyVersion("github.com/parnurzeal/gorequest")
	if len(os.Args) != 3 || os.Args[1] != version || !strings.HasPrefix(os.Args[2], "https://") {
		panic("usage: fpdb-gorequest 0.2.17 HTTPS_URL")
	}
	agent := gorequest.New()
	agent.Transport.Proxy = nil
	agent.Transport.DisableKeepAlives = true
	agent.Client.Timeout = 25 * time.Second
	agent.Client.CheckRedirect = func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }
	resp, body, errs := agent.Get(os.Args[2]).Set("Accept", "application/json").Set("Connection", "close").End()
	if len(errs) > 0 {
		panic(errs[0])
	}
	if resp == nil || resp.StatusCode < 200 || resp.StatusCode >= 300 {
		panic(fmt.Sprintf("bad HTTP status: %v", resp))
	}
	var echo interface{}
	if err := json.Unmarshal([]byte(body), &echo); err != nil {
		panic(err)
	}
	payload := map[string]interface{}{
		"runtime_version": strings.TrimPrefix(runtime.Version(), "go"), "client_version": version,
		"environment": map[string]interface{}{
			"os": runtime.GOOS, "arch": runtime.GOARCH,
			"tls_backend":  map[string]string{"name": "crypto/tls", "version": runtime.Version()},
			"dependencies": map[string]string{"gorequest": version},
		}, "echo": echo,
	}
	if err := json.NewEncoder(os.Stdout).Encode(payload); err != nil {
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
