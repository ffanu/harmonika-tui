package main

import (
	"context"
	"errors"
	"regexp"
	"testing"

	"github.com/DATA-DOG/go-sqlmock"
)

func translationReceiptTestDB(t *testing.T) (*app, sqlmock.Sqlmock) {
	t.Helper()
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := mock.ExpectationsWereMet(); err != nil {
			t.Error(err)
		}
		_ = db.Close()
	})
	return &app{db: db}, mock
}

func TestTranslationReceiptSavesOnlyTrustedOwnedAssistant(t *testing.T) {
	a, mock := translationReceiptTestDB(t)
	mock.ExpectExec(regexp.QuoteMeta(translationReceiptInsert)).WithArgs(int64(22), int64(11)).WillReturnResult(sqlmock.NewResult(0, 1))
	if !a.saveTranslationMessageReceipt(context.Background(), 11, 22, true, "v1") {
		t.Fatal("trusted receipt not saved")
	}
	// All other combinations must fail before database access.
	for _, tc := range []struct {
		user, message int64
		requested     bool
		header        string
	}{
		{11, 22, false, "v1"}, {11, 22, true, ""}, {11, 22, true, "V1"}, {11, 22, true, "v1 "},
		{0, 22, true, "v1"}, {11, 0, true, "v1"}, {-1, 22, true, "v1"}, {11, -1, true, "v1"},
	} {
		if a.saveTranslationMessageReceipt(context.Background(), tc.user, tc.message, tc.requested, tc.header) {
			t.Fatal("untrusted receipt saved")
		}
	}
}

func TestTranslationReceiptReadChecksBothOwnerAndMessage(t *testing.T) {
	a, mock := translationReceiptTestDB(t)
	for _, tc := range []struct {
		owner, message int64
		exists         bool
	}{
		{11, 22, true}, {99, 22, false}, {11, 99, false},
	} {
		mock.ExpectQuery(regexp.QuoteMeta(translationReceiptExists)).WithArgs(tc.owner, tc.message).WillReturnRows(sqlmock.NewRows([]string{"exists"}).AddRow(tc.exists))
		if got := a.hasTranslationMessageReceipt(context.Background(), tc.owner, tc.message); got != tc.exists {
			t.Fatal("owner/message receipt scope changed")
		}
	}
	if a.hasTranslationMessageReceipt(context.Background(), 0, 22) || a.hasTranslationMessageReceipt(context.Background(), 11, 0) {
		t.Fatal("invalid identity trusted")
	}
}

func TestTranslationReceiptDuplicateIsIdempotentAndCannotReassign(t *testing.T) {
	a, mock := translationReceiptTestDB(t)
	for _, tc := range []struct {
		owner  int64
		exists bool
	}{{11, true}, {99, false}} {
		mock.ExpectExec(regexp.QuoteMeta(translationReceiptInsert)).WithArgs(int64(22), tc.owner).WillReturnResult(sqlmock.NewResult(0, 0))
		mock.ExpectQuery(regexp.QuoteMeta(translationReceiptExists)).WithArgs(tc.owner, int64(22)).WillReturnRows(sqlmock.NewRows([]string{"exists"}).AddRow(tc.exists))
		if got := a.saveTranslationMessageReceipt(context.Background(), tc.owner, 22, true, "v1"); got != tc.exists {
			t.Fatal("duplicate receipt reassigned or lost")
		}
	}
}

func TestTranslationReceiptDatabaseErrorsFailClosed(t *testing.T) {
	a, mock := translationReceiptTestDB(t)
	privateErr := errors.New("SYNTHETIC_PRIVATE_DATABASE_ERROR")
	mock.ExpectExec(regexp.QuoteMeta(translationReceiptInsert)).WithArgs(int64(22), int64(11)).WillReturnError(privateErr)
	if a.saveTranslationMessageReceipt(context.Background(), 11, 22, true, "v1") {
		t.Fatal("failed insert trusted")
	}
	mock.ExpectExec(regexp.QuoteMeta(translationReceiptInsert)).WithArgs(int64(22), int64(11)).WillReturnResult(sqlmock.NewErrorResult(privateErr))
	if a.saveTranslationMessageReceipt(context.Background(), 11, 22, true, "v1") {
		t.Fatal("unknown insert outcome trusted")
	}
	mock.ExpectQuery(regexp.QuoteMeta(translationReceiptExists)).WithArgs(int64(11), int64(22)).WillReturnError(privateErr)
	if a.hasTranslationMessageReceipt(context.Background(), 11, 22) {
		t.Fatal("query error trusted")
	}
	mock.ExpectQuery(regexp.QuoteMeta(translationReceiptExists)).WithArgs(int64(11), int64(22)).WillReturnRows(sqlmock.NewRows([]string{"exists"}).AddRow("invalid-bool"))
	if a.hasTranslationMessageReceipt(context.Background(), 11, 22) {
		t.Fatal("invalid record trusted")
	}
}

func TestTranslationReceiptUnavailableDatabaseFailsClosed(t *testing.T) {
	for _, a := range []*app{nil, {}} {
		if a.saveTranslationMessageReceipt(context.Background(), 11, 22, true, "v1") || a.hasTranslationMessageReceipt(context.Background(), 11, 22) {
			t.Fatal("unavailable database trusted")
		}
	}
}
