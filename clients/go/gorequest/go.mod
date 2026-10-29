module fpdb/gorequest

go 1.24.0

require github.com/parnurzeal/gorequest v0.2.17

// Historical gorequest tags have no go.mod. Pin their undeclared dependencies
// so tidy does not select releases that require a newer Go toolchain.
require (
	github.com/elazarl/goproxy v1.9.2 // indirect
	github.com/moul/http2curl v1.0.0 // indirect
	github.com/pkg/errors v0.9.1 // indirect
	github.com/smartystreets/goconvey v1.8.1 // indirect
	golang.org/x/net v0.50.0 // indirect
	moul.io/http2curl v1.0.0 // indirect
)
