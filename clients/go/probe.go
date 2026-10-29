package main

import (
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"runtime"
	"strings"
	"time"
)

func main() {
	verify := os.Getenv("FPDB_TLS_VERIFY") != "false"
	if len(os.Args) != 3 || os.Args[1] != "stdlib" {
		panic("usage: probe.go stdlib URL")
	}
	transport := &http.Transport{Proxy: nil, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: !verify}, DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 25 * time.Second, CheckRedirect: func(r *http.Request, v []*http.Request) error { return http.ErrUseLastResponse }}
	req, err := http.NewRequest("GET", os.Args[2], nil)
	if err != nil {
		panic(err)
	}
	req.Header.Set("Accept", "application/json")
	req.Close = true
	resp, err := client.Do(req)
	if err != nil {
		panic(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		panic(fmt.Sprintf("status %d", resp.StatusCode))
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		panic(err)
	}
	var echo interface{}
	if err := json.Unmarshal(raw, &echo); err != nil {
		panic(err)
	}
	payload := map[string]interface{}{
		"runtime_version": strings.TrimPrefix(runtime.Version(), "go"), "client_version": "stdlib", "verify": verify,
		"environment": map[string]interface{}{"os": runtime.GOOS, "arch": runtime.GOARCH,
			"tls_backend": map[string]string{"name": "crypto/tls", "version": runtime.Version()}}, "echo": echo}
	if err := json.NewEncoder(os.Stdout).Encode(payload); err != nil {
		panic(err)
	}
}
