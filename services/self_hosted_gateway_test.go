package main

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"strings"
	"sync"
	"testing"
)

type selfHostedFailedTransport struct{}

func (selfHostedFailedTransport) RoundTrip(*http.Request) (*http.Response, error) {
	return nil, errors.New("SYNTHETIC_PRIVATE_WORKER_DETAILS")
}

func TestSelfHostedToolsConcurrentFailuresRemainIsolated(t *testing.T) {
	for _, tc := range []struct {
		name, code string
		proxy      *httputil.ReverseProxy
	}{
		{"webreader", "webreader_unavailable", webReaderProxy},
		{"semantic", "semantic_embedding_unavailable", semanticEmbeddingProxy},
		{"knowledge", "knowledge_unavailable", knowledgeProxy},
		{"rerank", "rerank_unavailable", rerankProxy},
		{"asr", "asr_unavailable", asrProxy},
		{"fileparser", "fileparser_unavailable", fileParserProxy},
		{"imagegen", "imagegen_unavailable", imageGenProxy},
		{"videogen", "videogen_unavailable", videoGenProxy},
		{"zread", "zread_unavailable", zreadProxy},
		{"safety", "safety_unavailable", safetyProxy},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Copy only to inject the fake transport; production globals remain unchanged.
			proxy := *tc.proxy
			proxy.Transport = selfHostedFailedTransport{}
			var group sync.WaitGroup
			for i := 0; i < 24; i++ {
				group.Add(1)
				go func() {
					defer group.Done()
					response := httptest.NewRecorder()
					proxy.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/synthetic", nil))
					if response.Code != http.StatusBadGateway || strings.Contains(response.Body.String(), "SYNTHETIC_PRIVATE") || !strings.Contains(response.Body.String(), tc.code) {
						t.Errorf("worker details leaked or error contract changed: %d", response.Code)
					}
				}()
			}
			group.Wait()
		})
	}
}

func TestSelfHostedToolsKeepAuthenticationBoundary(t *testing.T) {
	a := &app{}
	for _, tc := range []struct {
		name    string
		handler http.HandlerFunc
	}{
		{"webreader", a.webReaderAPI},
		{"semantic", a.semanticEmbeddingAPI},
		{"knowledge", a.knowledgeToolsAPI},
		{"rerank", a.rerankToolsAPI},
		{"asr", a.asrToolsAPI},
		{"fileparser", a.fileParserAPI},
		{"imagegen", a.imageGenAPI},
		{"videogen", a.videoGenAPI},
		{"zread", a.zreadToolsAPI},
		{"safety", a.safetyToolsAPI},
	} {
		t.Run(tc.name, func(t *testing.T) {
			response := httptest.NewRecorder()
			tc.handler(response, httptest.NewRequest(http.MethodGet, "/synthetic", nil))
			if response.Code != http.StatusUnauthorized {
				t.Fatalf("unauthenticated request reached worker: %d", response.Code)
			}
		})
	}
}
