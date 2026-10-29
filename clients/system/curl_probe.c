#include <curl/curl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/utsname.h>
#include <gnu/libc-version.h>
struct buffer {char *data; size_t len;};
static size_t append(char *data,size_t size,size_t count,void *arg){
 struct buffer *b=arg; size_t n=size*count;
 char *p=realloc(b->data,b->len+n+1);if(!p)return 0;
 b->data=p;memcpy(b->data+b->len,data,n);b->len+=n;b->data[b->len]=0;return n;
}
static void print_string(const char *s){
 putchar('"');for(;*s;s++) {unsigned char c=(unsigned char)*s;
  if(c=='"'||c=='\\'){putchar('\\');putchar(c);}
  else if(c=='\n'){fputs("\\n",stdout);}else if(c=='\r'){fputs("\\r",stdout);}
  else if(c<32){printf("\\u%04x",c);}else putchar(c);
 }putchar('"');
}
int main(int argc,char **argv){
 if(argc!=3||strcmp(argv[1],"system")){fprintf(stderr,"usage: curl_probe system URL\n");return 2;}
 CURL *h=curl_easy_init();if(!h)return 1;
 const char *mode=getenv("FPDB_TLS_VERIFY");int verify=!mode||strcmp(mode,"false");
 if(!verify){curl_easy_setopt(h,CURLOPT_SSL_VERIFYPEER,0L);curl_easy_setopt(h,CURLOPT_SSL_VERIFYHOST,0L);}
 struct buffer b={0};struct curl_slist *headers=NULL;
 headers=curl_slist_append(headers,"Accept: application/json");
 headers=curl_slist_append(headers,"Connection: close");
 curl_easy_setopt(h,CURLOPT_URL,argv[2]);curl_easy_setopt(h,CURLOPT_HTTPHEADER,headers);
 curl_easy_setopt(h,CURLOPT_PROXY,"");curl_easy_setopt(h,CURLOPT_NOPROXY,"*");
 const char *ca=getenv("SSL_CERT_FILE");if(ca&&*ca)curl_easy_setopt(h,CURLOPT_CAINFO,ca);
 curl_easy_setopt(h,CURLOPT_FOLLOWLOCATION,0L);curl_easy_setopt(h,CURLOPT_TIMEOUT,25L);
 curl_easy_setopt(h,CURLOPT_WRITEFUNCTION,append);curl_easy_setopt(h,CURLOPT_WRITEDATA,&b);
 CURLcode rc=curl_easy_perform(h);long status=0;curl_easy_getinfo(h,CURLINFO_RESPONSE_CODE,&status);
 if(rc!=CURLE_OK||status<200||status>=300||!b.data||b.data[0]!='{'){
  fprintf(stderr,"curl error: %s HTTP %ld\n",curl_easy_strerror(rc),status);return 1;
 }
 struct utsname un;if(uname(&un)){perror("uname");return 1;}
 curl_version_info_data *v=curl_version_info(CURLVERSION_NOW);
 printf("{\"runtime_version\":");print_string(gnu_get_libc_version());
 printf(",\"client_version\":\"system\",\"verify\":%s,\"environment\":{\"os\":",verify?"true":"false");print_string(un.sysname);
 printf(",\"arch\":");print_string(un.machine);
 printf(",\"tls_backend\":{\"name\":\"libcurl TLS\",\"version\":");print_string(v->ssl_version?v->ssl_version:"unknown");
 printf("},\"dependencies\":{\"libcurl\":");print_string(v->version);
 printf("}},\"echo\":%s}\n",b.data);
 curl_slist_free_all(headers);curl_easy_cleanup(h);free(b.data);return 0;
}
