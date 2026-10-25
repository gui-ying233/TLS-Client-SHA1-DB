package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"runtime"
	"strings"
	"time"

	"github.com/carlmjohnson/requests"
)

func main() {
	const version = "0.26.2"
	if len(os.Args) != 3 || os.Args[1] != version || !strings.HasPrefix(os.Args[2], "https://") {
		panic("usage: fpdb-requests 0.26.2 HTTPS_URL")
	}
	transport := &http.Transport{Proxy: nil, DisableKeepAlives: true, TLSHandshakeTimeout: 20 * time.Second}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 25 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }}
	var raw string
	err := requests.URL(os.Args[2]).Client(client).Header("Accept", "application/json").
		Header("Connection", "close").ToString(&raw).Fetch(context.Background())
	if err != nil {
		panic(err)
	}
	var echo interface{}
	if err = json.Unmarshal([]byte(raw), &echo); err != nil {
		panic(err)
	}
	payload := map[string]interface{}{
		"runtime_version": strings.TrimPrefix(runtime.Version(), "go"), "client_version": version,
		"environment": map[string]interface{}{
			"os": runtime.GOOS, "arch": runtime.GOARCH,
			"tls_backend":  map[string]string{"name": "crypto/tls", "version": runtime.Version()},
			"dependencies": map[string]string{"requests": version},
		}, "echo": echo,
	}
	if err = json.NewEncoder(os.Stdout).Encode(payload); err != nil {
		panic(fmt.Errorf("encode output: %w", err))
	}
}
