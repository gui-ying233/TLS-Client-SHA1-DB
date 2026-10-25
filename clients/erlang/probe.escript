#!/usr/bin/env escript
%%! -noshell

main(["stdlib", Url]) ->
    {ok, _} = application:ensure_all_started(ssl),
    {ok, _} = application:ensure_all_started(inets),
    Options = [{timeout, 25000}, {autoredirect, false},
               {ssl, [{verify, verify_peer},
                      {cacertfile, "/etc/ssl/certs/ca-certificates.crt"}]}],
    {ok, {{_, Status, _}, _, Body}} = httpc:request(
        get, {Url, [{"accept", "application/json"}, {"connection", "close"}]},
        Options, [{body_format, binary}]),
    true = Status >= 200 andalso Status < 300,
    {ok, SSLVersion} = application:get_key(ssl, vsn),
    Payload = #{
        <<"runtime_version">> => list_to_binary(erlang:system_info(otp_release)),
        <<"client_version">> => <<"stdlib">>,
        <<"environment">> => #{
            <<"os">> => <<"linux">>,
            <<"arch">> => list_to_binary(erlang:system_info(system_architecture)),
            <<"tls_backend">> => #{
                <<"name">> => <<"OTP ssl">>,
                <<"version">> => list_to_binary(SSLVersion)}},
        <<"echo">> => json:decode(Body)},
    io:put_chars([json:encode(Payload), "\n"]);
main(_) -> erlang:error(bad_arguments).
